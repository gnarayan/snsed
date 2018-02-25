#! /usr/bin/env python
#S.rodney & J.R. Pierel
# 2017.03.31

import os,sys,getopt,warnings,math,glob,shutil,sncosmo,multiprocessing,snsedextend
from numpy import *
from pylab import * 
from scipy import interpolate as scint
from scipy import stats
from scipy.integrate import simps
from astropy.table import Table,Column,MaskedColumn
from multiprocessing import Pool
from collections import OrderedDict as odict
from scipy.interpolate import RectBivariateSpline

__all__=['extendNon1a','bandRegister','curveToColor']

#Automatically sets environment to your SNDATA_ROOT file (Assumes you've set this environment variable,otherwise will use the builtin version)
try:
    sndataroot = os.environ['SNDATA_ROOT']
except:
    os.environ['SNDATA_ROOT']=os.path.join(os.path.dirname(snsedextend),'SNDATA_ROOT')
    sndataroot=os.environ['SNDATA_ROOT']

#default transmission files, user can define their own 
_filters={
    'U':'bessellux',
    'B':'bessellb',
    'V':'bessellv',
    'R':'bessellr',
    'I':'besselli',
    'J':'paritel::j',
    'H':'paritel::h',
    'K':'paritel::ks',
    'Ks':'paritel::ks',
    'u':'sdss::u',
    'g':'sdss::g',
    'r':'sdss::r',
    'i':'sdss::i',
    'Z':'sdss::z'
}

_props=odict([
    ('time',{'mjd', 'mjdobs', 'jd', 'time', 'date', 'mjd_obs','mhjd','jds','mjds'}),
    ('band',{'filter', 'band', 'flt', 'bandpass'}),
    ('flux',{'flux', 'f','fluxes'}),
    ('fluxerr',{'flux_error', 'fluxerr', 'fluxerror', 'fe', 'flux_err','fluxerrs'}),
    ('zp',{'zero_point','zp', 'zpt', 'zeropoint'}),
    ('zpsys',{'zpsys', 'magsys', 'zpmagsys'}),
    ('mag',{'mag','magnitude','mags'}),
    ('magerr',{'magerr','magerror','magnitudeerror','magnitudeerr','magerrs','dmag'})
])

#dictionary of zero-points (calculated later based on transmission files)
_zp={}

_needs_bounds={'z'}


_UVrightBound=4000
_UVleftBound=1200
_IRleftBound=9000
_IRrightBound=55000



class snSource(sncosmo.Source):

    _param_names = ['amplitude']
    param_names_latex = ['A']   # used in plotting display

    def __init__(self, phase, wave, flux1, name=None, version=None):
        self.name = name
        self.version = version
        self._phase = phase
        self._wave = wave

        self._model_flux1 = RectBivariateSpline(phase, wave, flux1, kx=3, ky=3)
        self._parameters = np.array([1.0])  # initial parameters

    def _flux(self, phase, wave):
        amplitude = self._parameters
        return amplitude  * self._model_flux1(phase, wave)

def _standardize(table):
    for col in table.colnames:
        if '-' not in col and '_' not in col:
            if col != _get_default_prop_name(col.lower()):
                table.rename_column(col, _get_default_prop_name(col.lower()))
    return(table)

def _findFile(filename):
    """Helper function that does a quick recurisive directory seach for a transmission file.
    :param filename: Transmission file to find
    """
    for root, subFolders, files in os.walk('.'):
        if filename in files:
            return(os.path.abspath(os.path.join(root,filename)))

def _getsed(sedfile) : 
    """
    Helper function that reads an SED file into a useable format.
    :param sedfile: SED filename to read (string)
    """

    d,w,f = loadtxt( sedfile, unpack=True,comments='#') 
    
    days = unique( d ) 

    dlist = [ d[ where( d == day ) ] for day in days ]
    wlist = [ w[ where( d == day ) ] for day in days ]
    flist = [ f[ where( d == day ) ] for day in days ]

    return( dlist, wlist, flist )

def _get_default_prop_name(prop):
    for key,value in _props.items():
        if {prop.lower()} & value:
            return key
    return prop

def _getZP(band,zpsys):
    """
    Helper function that calculates the zero-point of a given band
    :param band: filename of band transmission function to get zero-point of 
    """
    ab=sncosmo.get_magsystem(zpsys)
    return(ab.band_flux_to_mag(1,band))


def _find_nearest(array,value):
    """
    Helper function to find the nearest value in an array
    :param array: The array to search
    :param value: The value you want to match
    """

    idx = np.searchsorted(array, value, side="left")
    if idx > 0 and (idx == len(array) or math.fabs(value - array[idx-1]) < math.fabs(value - array[idx])):
        return idx-1,array[idx-1]
    else:
        return idx,array[idx]

def _getFilter(band):
    w,t=loadtxt(_filters[band],unpack=True,comments='#')
    return(w,t)

def plotsed( sedfile,day, normalize=False,MINWAVE=None,MAXWAVE=None,**kwarg): 
    """
    Simple plotting function to visualize the SED file.
    :param sedfile: SED filename to read (string)
    :param day: The day you want to plot, or 'all'
    :param normalize: Boolean to normalize the data
    :param MINWAVE: Allows user to plot in a specific window, left edge
    :param MAXWAVE: Allows user to plot in a specific window, right edge
    """

    dlist,wlist,flist = _getsed( sedfile ) 

    for i in range( len(wlist) ) : 
        if MINWAVE:
            idx1,val= _find_nearest(wlist[i],MINWAVE)
        else:
            idx1=None
        if MAXWAVE:
            idx2,val=_find_nearest(wlist[i],MAXWAVE)
        else:
            idx2=None

        thisday = dlist[i][0]

        if day!='all' : 
            if abs(thisday-day)>0.6 : continue
        if normalize : 
            plot( wlist[i], flist[i]/flist[i].max()+thisday, **kwarg )
            show()
        else : 
            plot( wlist[i][idx1:idx2], flist[i][idx1:idx2], label=str(thisday), **kwarg )
            show()

def _extrapolate_uv(band,rArea,color,xTrans,xWave,f,w,niter,log,index,bandDict,zpsys,accuracy=1e-9):
    """
    Algorithm for extrapolation of SED into the UV
    """
    wavestep=w[1]-w[0]
    idx,val=_find_nearest(w,xWave[-1])
    idx2,val2=_find_nearest(w,xWave[0])
    if val-xWave[-1]>wavestep: #then need to extrapolate between end of SED and left edge of band
        Nstep = len( arange(xWave[-1], val, wavestep ) )
        wextRed =  sorted( [ xWave[-1] + (j+1)*wavestep for j in range(Nstep) ] )
        fextRed = array( [ max(0,f[idx]) for wave in wextRed ] )#just a flat slope from the end of the SED to the edge of the band
        w2 = append(wextRed,w[idx:])
        f2 = append(fextRed,f[idx:])

    else: #if the SED extends to the band, then cut it off at the band for new extrapolation
        w2=w[idx:]
        f2=f[idx:]
    if idx > idx2:
        w1=w[:idx2]
        f1=f[:idx2]
    else:
        w1=[]
        f1=[]
    w=w2
    f=f2
    ms=sncosmo.get_magsystem(zpsys)
    #print(rArea)
    bArea= ms.band_mag_to_flux(rArea+color,bandDict[band])*sncosmo.constants.HC_ERG_AA #area in the band we are extrapolating into (mag to flux)
    #bArea=rArea*color
    x1=w[0]
    x2=xWave[0]
    interpFunc=scint.interp1d(append(x2,x1),append(0,f[0]))
    #area=simps(xTrans*interpFunc(xWave),dx=wavestep) #this is the area if the slope is such that the flux is 0 at the right edge of the band
    area=np.sum(interpFunc(xWave)*xTrans*xWave*gradient(xWave))
    i=1
    extra=False
    #print(color,bArea,area)
    if area>bArea: #then we need flux=0, but slope of line steeper
        i=-1
        while area>bArea:
            i+=1
            x2=xWave[i]
            interpFunc=scint.interp1d(append(x2,w[0]),append(0,f[0]))
            idx=list(xWave).index(x2)
            #print(len(xTrans[0:idx+1]),len(arange(w[-1],x2+wavestep/10,wavestep)))
            area=np.sum(interpFunc(arange(x2,w[0]+wavestep/10,wavestep))*xTrans[idx:]*arange(x2,w[0]+wavestep/10,wavestep)*gradient(arange(x2,w[0]+wavestep/10,wavestep)))
        i=1
        x3=x2-wavestep
        idx2=list(xWave).index(x2)
        idx3=idx2-1
        while(abs(area-bArea)/(area+bArea)>accuracy and i<=niter): #this loop runs if necessary accuracy wasn't reached because of wavelength binning, it changes the starting flux slightly
            if i==1:
                y3=f[0]/2
                while area<bArea:
                    y3*=2
                    interpFunc1=scint.interp1d(append(x2,w[0]),append(y3,f[0]))
                    interpFunc2=scint.interp1d(append(x3,x2),append(0,y3))
                    #area=simps(xTrans[0:idx+1]*interpFunc(arange(w[-1],x2+wavestep/10,wavestep)),dx=wavestep)
                    area1=np.sum(interpFunc1(arange(x2,w[0]+wavestep/10,wavestep))*xTrans[idx:]*arange(x2,w[0]+wavestep/10,wavestep)*gradient(arange(x2,w[0]+wavestep/10,wavestep)))
                    #print(len(arange(x2,x3+wavestep/10,wavestep)))
                    area2=np.sum(interpFunc2(arange(x3,x2+wavestep/10,wavestep))*xTrans[idx3:idx2+1]*arange(x3,x2+wavestep/10,wavestep)*gradient(arange(x3,x2+wavestep/10,wavestep)))
                    area=area1+area2
                y1=0
                y2=y3
            if area>bArea:
                y3=y2
            else:
                y1=y2
            y2=(y3+y1)/2
            interpFunc1=scint.interp1d(append(x2,w[0]),append(y2,f[0]))
            interpFunc2=scint.interp1d(append(x3,x2),append(0,y2))
            #area=simps(xTrans[0:idx+1]*interpFunc(arange(w[-1],x2+wavestep/10,wavestep)),dx=wavestep)
            area1=np.sum(interpFunc1(arange(x2,w[0]+wavestep/10,wavestep))*xTrans[idx:]*arange(x2,w[0]+wavestep/10,wavestep)*gradient(arange(x2,w[0]+wavestep/10,wavestep)))
            #print(len(arange(x2,x3+wavestep/10,wavestep)))
            area2=np.sum(interpFunc2(arange(x3,x2+wavestep/10,wavestep))*xTrans[idx3:idx2+1]*arange(x3,x2+wavestep/10,wavestep)*gradient(arange(x3,x2+wavestep/10,wavestep)))
            area=area1+area2
            i+=1
        y1=f[0]
        extra=True
        '''
        y1=f[0]
        y2=0
        last=0
        while(abs((area-bArea)/(area+bArea))>accuracy and i<=niter):

            if area>bArea:
                x3=x2
            else:
                x1=x2
            x2=(x3+x1)/2
            idx,x2=_find_nearest(xWave,x2)
            interpFunc=scint.interp1d(append(x2,w[0]),append(0,f[0]))
            #area=simps(xTrans[idx:]*interpFunc(arange(x2,w[0]+wavestep/10,wavestep)),dx=wavestep)
            area=np.sum(interpFunc(arange(x2,w[0]+wavestep/10,wavestep))*xTrans[idx:]*arange(x2,w[0]+wavestep/10,wavestep)*gradient(arange(x2,w[0]+wavestep/10,wavestep)))
            i+=1
            if area==last: #break out of loop once we can't get more accurate because of wavelength binning
                break
            last=area
        if abs((area-bArea)/(area+bArea))>accuracy and i<=niter:
            i=1
            while(abs((area-bArea)/(area+bArea))>accuracy and i<=niter): #this loop runs if necessary accuracy wasn't reached because of wavelength binning, it changes the starting flux slightly
                if i==1:
                    y3=f[0]
                    y1=0
                    while area<bArea:
                        y3*=2
                        interpFunc=scint.interp1d(append(x2,w[0]),append(0,y3))
                        #area=simps(xTrans[idx:]*interpFunc(arange(x2,w[0]+wavestep/10,wavestep)),dx=wavestep)
                        area=np.sum(interpFunc(arange(x2,w[0]+wavestep/10,wavestep))*xTrans[idx:]*arange(x2,w[0]+wavestep/10,wavestep)*gradient(arange(x2,w[0]+wavestep/10,wavestep)))
                        y1=f[0]
                    y2=y3
                if area>bArea:
                    y3=y2
                else:
                    y1=y2
                y2=(y3+y1)/2
                interpFunc=scint.interp1d(append(x2,w[0]),append(0,y2))
                area=np.sum(interpFunc(arange(x2,w[0]+wavestep/10,wavestep))*xTrans[idx:]*arange(x2,w[0]+wavestep/10,wavestep)*gradient(arange(x2,w[0]+wavestep/10,wavestep)))
                i+=1
            y1=y2
            y2=0
    '''
    elif area<bArea:#then the flux at the left edge of the band must be greater than 0
        y1=0
        y3=max(f)#initial upper bound is max of SED
        y2=y3
        x2=xWave[0]
        tried=False
        while(abs(area-bArea)/(area+bArea)>accuracy and i<=niter):
            if area>bArea:
                y3=y2
            else:
                if not tried:
                    while area < bArea:#this will increase the upper bound until the area is greater than the necessary area
                        y3*=2
                        interpFunc=scint.interp1d(append(x2,w[0]),append(y3,f[0]))
                        #area=simps(xTrans*interpFunc(xWave),dx=wavestep)
                        area=np.sum(interpFunc(xWave)*xTrans*xWave*gradient(xWave))
                    tried=True
                else:
                    y1=y2
            y2=(y3+y1)/2

            interpFunc=scint.interp1d(append(x2,w[0]),append(y2,f[0]))
            #area=simps(xTrans*interpFunc(xWave),dx=wavestep)
            area=np.sum(interpFunc(xWave)*xTrans*xWave*gradient(xWave))
            i+=1
        y1=f[0]
    else:
        y1=f[0]
        y2=0
    if abs(area-bArea)>.001*bArea:
        log.write('WARNING: The parameters you chose led to an integration in the %s-Band of %e instead of %e for index %i \n'%(band,vArea/area,color,index))
    (a,b,rval,pval,stderr)=stats.linregress(append(x2,w[0]),append(y2,y1))
    if extra:

        Nstep3 = len( arange( x2,w[0],  wavestep ) )
        wextRed3 =  sorted( [ x2 + (j+1)*wavestep for j in range(Nstep3) ])
        fextRed3 = array( [ max( 0, a * wave + b ) for wave in wextRed3 ] )
        if x3>xWave[0]:
            Nstep1=len(arange(  xWave[0],x3,  wavestep ) )
            wextRed1 =  sorted( [ xWave[0] + (j+1)*wavestep for j in range(Nstep1) ])
            fextRed1 = array( [ 0 for wave in wextRed1 ] )
        else:
            wextRed1=[]
            fextRed1=[]

        (a,b,rval,pval,stderr)=stats.linregress(append(x3,x2),append(0,y2))
        Nstep2=len(arange( x3, x2,  wavestep ) )
        wextRed2 =  sorted( [ x3 + (j+1)*wavestep for j in range(Nstep2) ])
        fextRed2 = array( [ max( 0, a * wave + b ) for wave in wextRed2 ] )



        wextRed=np.append(wextRed1,np.append(wextRed2,wextRed3))
        fextRed=np.append(fextRed1,np.append(fextRed2,fextRed3))
    else:
        Nstep = len( arange( xWave[0],w[0],  wavestep ) )
        wextRed =  sorted( [ xWave[0] + (j)*wavestep for j in range(Nstep) ] )
        fextRed = array( [ max( 0, a * wave + b ) if wave >= xWave[0] else max(0,a*xWave[0]+b) for wave in wextRed ] )


    w = append(w1, append(wextRed,w))
    f = append(f1, append(fextRed,f))

    return(w,f)

def _createSED(filename,rescale=False):
    phase,wave,flux=sncosmo.read_griddata_ascii(filename)
    #phase,wave,flux=_getsed(filename)

    #print(p.shape,w.shape,f.shape)
    #print(wave[-500:]-arange(wave[-500],wave[-1]+1,10))
    #print(phase.shape,wave.shape,flux.shape)
    #print(wave[-250:-230])
    if rescale:
        for i in range( len(phase) ) :
            flux[i]=flux[i]/sncosmo.constants.HC_ERG_AA
        #flux=flux/(sncosmo.constants.HC_ERG_AA/wave)
    return(snSource(phase,wave,flux,name='extrapolated'))

def _getPartialBandMag(model,phase,wave,band,zpsys,wavestep):
    interpFunc=scint.interp1d(band.wave,band.trans)
    #idx,val=_find_nearest(wave,int(math.floor(band.wave[0]/wavestep))*wavestep)
    waves=arange(band.wave[0],wave[-1]+1,wavestep)
    transInterp=interpFunc(waves)
    #git test
    sncosmo.registry.register(sncosmo.Bandpass(waves,transInterp,name='tempBand'),force=True)

    return(model.bandmag('tempBand',zpsys,phase)) #magnitude in the overlap of the band

#def _getPartialBandFlux(model)

def _checkBandOverlap(band1,band2):
    if (band1.wave[0]<band2.wave[0] and band1.wave[-1]>band2.wave[0]) or (band2.wave[0]<band1.wave[0] and band2.wave[-1]>band1.wave[0]):
        return True
    return False

def _extrapolate_ir(band,vArea,color,xTrans,xWave,d,f,w,niter,log,index,doneIR,bandDict,zpsys,model,bandsDone,accuracy=1e-12):
    """
    Algorithm for extrapolation of SED into the IR
    """
    wavestep=w[1]-w[0]
    idx,val=_find_nearest(w,xWave[0]) #closest wavelength in the sed to the start of the band
    idx2,val2=_find_nearest(w,xWave[-1]) #closest wavelength in the sed to the end of the band
    overlapMag=0
    temp1=xTrans[xWave<=w[-1]]
    temp=xWave[xWave<=w[-1]]
    if xWave[0]-val>wavestep: #then need to extrapolate between end of SED and left edge of band
        Nstep = len( arange( val, xWave[0],  wavestep ) )
        wextRed =  sorted( [ val + (j+1)*wavestep for j in range(Nstep) ] )
        fextRed = array( [ max(0,f[idx]) for wave in wextRed ] )#just a flat slope from the end of the SED to the edge of the band
        w2 = append(w[:idx+1], wextRed)
        f2 = append(f[:idx+1], fextRed)
    else: #if the SED extends to the band:
        if not any([_checkBandOverlap(bandDict[x],bandDict[band]) for x in bandsDone]): #  then cut it off at the band for new extrapolation
            w2=w[:idx+1]
            f2=f[:idx+1]
        else:#calculate the area for the overlap between bands, then subtract that from what is necessary and begin extrapolation at the right edge of the last extrapolation
            w2=w
            f2=f
            bandIdx,bandVal=_find_nearest(xWave,w[-1])#int(math.ceil(wave1[0]/wavestep))*wavestep
            tempIdx,tempVal=_find_nearest(w,xWave[0])
            if xWave[0]<w[-1]:
                '''
                if np.max(f[tempIdx:])>0:
                    #overlapMag=_getPartialBandMag(model,d[0],w,bandDict[band],zpsys,wavestep)
                else:
                    overlapMag=0
                '''
                if bandVal<w[-1]:
                    xTrans=xTrans[bandIdx+1:]
                    xWave=xWave[bandIdx+1:]
                else:
                    xTrans=xTrans[bandIdx:]
                    xWave=xWave[bandIdx:]
                '''
                tempIdx2,tempVal2=_find_nearest(w,xWave[0])
                if xWave[0]-w[-1]>wavestep: #then need to extrapolate between end of SED and left edge of band
                    Nstep = len( arange( val, xWave[0],  wavestep ) )
                    wextRed =  sorted( [ val + (j+1)*wavestep for j in range(Nstep) ] )
                    fextRed = array( [ max(0,f[idx]) for wave in wextRed ] )#just a flat slope from the end of the SED to the edge of the band
                    w2 = append(w[:tempIdx2+1], wextRed)
                    f2 = append(f[:tempIdx2+1], fextRed)
                '''

    w=w2
    f=f2
    ms=sncosmo.get_magsystem(zpsys)

    try:
        overlapArea=np.sum(f[tempIdx:]*temp*temp1*gradient(temp))
    except:
        overlapArea=0

    bArea= ms.band_mag_to_flux(vArea-color,bandDict[band])*sncosmo.constants.HC_ERG_AA-overlapArea #area in the band we are extrapolating into (mag to flux), everything should be in ergs at this point
    #bArea=vArea/color
    x2=xWave[-1]
    x1=w[-1]
    interpFunc=scint.interp1d(append(x1,x2),append(f[-1],0))
    #interpFunc2=scint.interp1d(bandDict[band].wave,bandDict[band].dwave)
    #area=simps(xTrans*interpFunc(xWave),dx=wavestep) #this is the area if the slope is such that the flux is 0 at the right edge of the band
    area=np.sum(interpFunc(xWave)*xTrans*xWave*gradient(xWave))
    i=1
    #print(d[0],color)
    extra=False
    #print(bArea,area)
    if area>bArea: #then we need flux=0, but slope of line steeper
        '''
        y1=f[-1]
        y2=0
        x1=w[-1]
        x2=2*bArea/y1
        '''
        #last=0
        i=0
        while area>bArea:
            i-=1
            x2=xWave[i]
            interpFunc=scint.interp1d(append(w[-1],x2),append(f[-1],0))
            idx=list(xWave).index(x2)
            #print(len(xTrans[0:idx+1]),len(arange(w[-1],x2+wavestep/10,wavestep)))
            area=np.sum(interpFunc(arange(w[-1],x2+wavestep/10,wavestep))*xTrans[:idx+1]*arange(w[-1],x2+wavestep/10,wavestep)*gradient(arange(w[-1],x2+wavestep/10,wavestep)))
        i=1
        x3=x2+wavestep
        idx2=list(xWave).index(x2)
        idx3=idx2+1
        while(abs(area-bArea)/(area+bArea)>accuracy and i<=niter): #this loop runs if necessary accuracy wasn't reached because of wavelength binning, it changes the starting flux slightly
            if i==1:
                y3=f[-1]/2
                while area<bArea:
                    y3*=2
                    interpFunc1=scint.interp1d(append(w[-1],x2),append(f[-1],y3))
                    interpFunc2=scint.interp1d(append(x2,x3),append(y3,0))
                    #area=simps(xTrans[0:idx+1]*interpFunc(arange(w[-1],x2+wavestep/10,wavestep)),dx=wavestep)
                    area1=np.sum(interpFunc1(arange(w[-1],x2+wavestep/10,wavestep))*xTrans[:idx2+1]*arange(w[-1],x2+wavestep/10,wavestep)*gradient(arange(w[-1],x2+wavestep/10,wavestep)))
                    #print(len(arange(x2,x3+wavestep/10,wavestep)))
                    area2=np.sum(interpFunc2(arange(x2,x3+wavestep/10,wavestep))*xTrans[idx2:idx3+1]*arange(x2,x3+wavestep/10,wavestep)*gradient(arange(x2,x3+wavestep/10,wavestep)))
                    area=area1+area2
                y1=0
                y2=y3
            if area>bArea:
                y3=y2
            else:
                y1=y2
            y2=(y3+y1)/2
            interpFunc1=scint.interp1d(append(w[-1],x2),append(f[-1],y2))
            interpFunc2=scint.interp1d(append(x2,x3),append(y3,0))
            #area=simps(xTrans[0:idx+1]*interpFunc(arange(w[-1],x2+wavestep/10,wavestep)),dx=wavestep)
            area1=np.sum(interpFunc1(arange(w[-1],x2+wavestep/10,wavestep))*xTrans[0:idx2+1]*arange(w[-1],x2+wavestep/10,wavestep)*gradient(arange(w[-1],x2+wavestep/10,wavestep)))
            area2=np.sum(interpFunc2(arange(x2,x3+wavestep/10,wavestep))*xTrans[idx2:idx3+1]*arange(x2,x3+wavestep/10,wavestep)*gradient(arange(x2,x3+wavestep/10,wavestep)))
            area=area1+area2
            i+=1
        y1=f[-1]
        extra=True
        '''
        while(abs(area-bArea)/(area+bArea)>accuracy and i<=niter):
            if area>bArea:
                3=x2
            else:
                x1=x2
            x2=(x3+x1)/2
            #idx,x2=_find_nearest(xWave,x2)
            interpFunc1=scint.interp1d(append(w[-1],x2),append(f[-1],0))
            interpFunc2=scint.interp1d(xWave,xTrans)
            #area=simps(xTrans[0:idx+1]*interpFunc(arange(w[-1],x2+wavestep/10,wavestep)),dx=wavestep)
            area=np.sum(interpFunc(arange(w[-1],x2+wavestep/10,wavestep))*xTrans[0:idx+1]*arange(w[-1],x2+wavestep/10,wavestep)*gradient(arange(w[-1],x2+wavestep/10,wavestep)))
            i+=1

        w3=[int(math.ceil(x2/wavestep))*wavestep]
            #if area==last: #break out of loop once we can't get more accurate because of wavelength binning
            #    break
            #last=area
        
        if abs(area-bArea)/(area+bArea)>accuracy and i<=niter:
            print('or here?')
            i=1
            while(abs(area-bArea)/(area+bArea)>accuracy and i<=niter): #this loop runs if necessary accuracy wasn't reached because of wavelength binning, it changes the starting flux slightly
                if i==1:
                    y3=f[-1]
                    while area<bArea:
                        y3*=2
                        interpFunc=scint.interp1d(append(w[-1],x2),append(y3,0))
                        #area=simps(xTrans[0:idx+1]*interpFunc(arange(w[-1],x2+wavestep/10,wavestep)),dx=wavestep)
                        area=np.sum(interpFunc(arange(w[-1],x2+wavestep/10,wavestep))*xTrans[0:idx+1]*arange(w[-1],x2+wavestep/10,wavestep)*gradient(arange(w[-1],x2+wavestep/10,wavestep)))
                    y1=f[-1]
                    y2=y3
                if area>bArea:
                    y3=y2
                else:
                    y1=y2
                y2=(y3+y1)/2
                interpFunc=scint.interp1d(append(w[-1],x2),append(y2,0))
                #area=simps(xTrans[0:idx+1]*interpFunc(arange(w[-1],x2+wavestep/10,wavestep)),dx=wavestep)
                area=np.sum(interpFunc(arange(w[-1],x2+wavestep/10,wavestep))*xTrans[0:idx+1]*arange(w[-1],x2+wavestep/10,wavestep)*gradient(arange(w[-1],x2+wavestep/10,wavestep)))
                i+=1
            y1=y2
            y2=0
            print(i)
        '''
    elif area<bArea:#then the flux at the right edge of the band must be greater than 0
        y1=0
        y3=max(f)#initial upper bound is max of SED
        y2=y3
        x2=xWave[-1]
        tried=False
        while(abs(area-bArea)/(area+bArea)>accuracy and i<=niter):
            if area>bArea:
                y3=y2
            else:
                if not tried:
                    while area < bArea:#this will increase the upper bound until the area is greater than the necessary area
                        y3*=2
                        interpFunc=scint.interp1d(append(w[-1],x2),append(f[-1],y3))
                        #area=simps(xTrans*interpFunc(xWave),dx=wavestep)
                        area=np.sum(interpFunc(xWave)*xTrans*xWave*gradient(xWave))
                    tried=True
                else:
                    y1=y2
            y2=(y3+y1)/2

            interpFunc=scint.interp1d(append(w[-1],x2),append(f[-1],y2))
            #area=simps(xTrans*interpFunc(xWave),dx=wavestep)
            area=np.sum(interpFunc(xWave)*xTrans*xWave*gradient(xWave))
            i+=1
        y1=f[-1]
    else:
        y1=f[-1]
        y2=0
    #print(ms.band_flux_to_mag((area+overlapArea)/sncosmo.constants.HC_ERG_AA,bandDict[band]))
    if abs(area-bArea)>.001*bArea:
        log.write('WARNING: The parameters you chose led to an integration in the %s-Band of %e instead of %e for index %i \n'%(band,vArea-ms.band_flux_to_mag((area+overlapArea)/sncosmo.constants.HC_ERG_AA,bandDict[band]),color,index))
    (a,b,rval,pval,stderr)=stats.linregress(append(w[-1],x2),append(y1,y2))


    if extra:
        Nstep1 = len( arange( w[-1], x2,  wavestep ) )
        wextRed1 =  sorted( [ w[-1] + (j+1)*wavestep for j in range(Nstep1) ])
        fextRed1 = array( [ max( 0, a * wave + b ) for wave in wextRed1 ] )
        (a,b,rval,pval,stderr)=stats.linregress(append(x2,x3),append(y2,0))
        Nstep2=len(arange( x2, x3,  wavestep ) )
        wextRed2 =  sorted( [ x2 + (j+1)*wavestep for j in range(Nstep2) ])
        fextRed2 = array( [ max( 0, a * wave + b ) for wave in wextRed2 ] )
        if x3<xWave[-1]:
            Nstep3=len(arange( x3, xWave[-1],  wavestep ) )
            wextRed3 =  sorted( [ x3 + (j+1)*wavestep for j in range(Nstep3) ])
            fextRed3 = array( [ 0 for wave in wextRed3 ] )
        else:
            wextRed3=[]
            fextRed3=[]
        wextRed=np.append(wextRed1,np.append(wextRed2,wextRed3))
        fextRed=np.append(fextRed1,np.append(fextRed2,fextRed3))
    else:
        Nstep = len( arange( w[-1], xWave[-1],  wavestep ) )
        wextRed =  sorted( [ w[-1] + (j+1)*wavestep for j in range(Nstep) ] )

        fextRed = array( [ max( 0, a * wave + b ) if wave <= xWave[-1] else max(0,a*xWave[-1]+b) for wave in wextRed ] )

    w = append(w, wextRed)
    f = append(f, fextRed)
    return(w,f)

'''
def _getBestModel(table,ir):
    import pandas as pd
    import pymc3 as pm
    import snsedextend


    modelList=['k1','k2']

    temp=pd.DataFrame({'x':np.array(table['time']),'y':np.array(table['mag']),'error':np.array(table['magerr'])})
    temp_xlims = (temp['x'].min() - np.ptp(temp['x'])/10,temp['x'].max() + np.ptp(temp['x'])/10)
    models_lin,traces_lin=snsedextend.run_models(temp,2)
    #print([name for name,thing in inspect.getmembers(models_lin['k2'].model)])

    dfdic = pd.DataFrame(index=modelList, columns=['dic','waic'])
    dfdic.index.name = 'model'

    for nm in dfdic.index:
        dfdic.loc[nm, 'dic'] = pm.stats.dic(traces_lin[nm], models_lin[nm])
        dfdic.loc[nm, 'waic'] = pm.stats.waic(traces_lin[nm], models_lin[nm])[0]

    dfdic = pd.melt(dfdic.reset_index(), id_vars=['model'], var_name='poly', value_name='Information Criterion')

    #g = sns.factorplot(x='model', y='Information Criterion', col='poly', hue='poly', data=dfdic, kind='bar', size=6)
    #plt.show()
    dfwaic = pd.DataFrame(index=modelList, columns=['lin'])
    dfwaic.index.name = 'model'

    for nm in dfwaic.index:
        dfwaic.loc[nm, 'lin'] = pm.stats.waic(traces_lin[nm], models_lin[nm])[0]

    best=dfwaic[dfwaic['lin']==np.min(dfwaic['lin'])].index[0]
    dfwaic = pd.melt(dfwaic.reset_index(), id_vars=['model'], var_name=ir.upper(), value_name='waic')

    snsedextend.plot_posterior_cr(models_lin,traces_lin,temp,temp_xlims,datamodelnm=ir, bestModel=best,modelnms=modelList,typ=type,bic=dfwaic)
'''

def _extrapolatesed(sedfile, newsedfile,color,table,time,modColor, bands,zpsys,bandsDone,niter=50):
    """ Interpolate the given transmission function to the wavestep of the SED, then
    get the area in the V band for color calculation and run the extrapolation algorithm.
    :param sedfile: SED file being extrapolated
    :param newsedfile: filename of output SED file
    :param fVH 
    """
    #dlist,wlist,flist = _getsed( sedfile ) #first time this is read in, should be in ergs/s/cm^2/AA

    tempTime=[]
    tempColor=[]
    extrap=True
    
    if dlist[0][0]<table[_get_default_prop_name('time')][0]:
        tempTime=append(math.floor(dlist[0][0]),time)
        tempColor=append(table[color][0],modColor)
    else:
        extrap=False
    if dlist[-1][0]>time[-1]:
        if np.any(tempTime):
            tempTime=append(tempTime,math.ceil(dlist[-1][0]))
            tempColor=append(tempColor,modColor[-1])
        else:
            tempTime=append(tempTime,append(time,math.ceil(dlist[-1][0])))
            tempColor=append(tempColor,append(table[color][-1],modColor))
    elif dlist[-1][0]<=time[-1] and not extrap:
        extrap=False
    if not extrap:
        tempTime=time
        tempColor=modColor

    i=0

    while dlist[i][0]<time[0]:
        i+=1
    dlist=dlist[i:]
    wlist=wlist[i:]
    flist=flist[i:]
    i=-1
    while dlist[i][0]>time[-1]:
        i-=1
    if i!=-1:
        dlist=dlist[:i+1]
        wlist=wlist[:i+1]
        flist=flist[:i+1]


    blue=color[0]
    red=color[-1]
    bWave=bands[blue].wave
    bTrans=bands[blue].trans
    rWave=bands[red].wave
    rTrans=bands[red].trans
    bInterpFunc=scint.interp1d(bWave,bTrans)
    rInterpFunc=scint.interp1d(rWave,rTrans)
    cInterpFunc=scint.interp1d(time,modColor)
    tempTime=[x[0] for x in dlist]
    colorData=cInterpFunc(tempTime)

    sed=_createSED(sedfile,rescale=False) #now the original sed is in photons/s/cm^2
    model=sncosmo.Model(sed)

    fout = open( newsedfile, 'w' )
    log= open('./error.log','w')

    def _extrap_helper(wave1,interpFunc1,wave2,interpFunc2,known,currentPhase):
        #idx,val=_find_nearest(w,int(math.ceil(wave1[0]/wavestep))*wavestep)
        #idx2,val2=_find_nearest(w,int(math.floor(wave1[-1]/wavestep))*wavestep)
        #interp=interpFunc1(arange(val,val2+1,wavestep))
        #area=simps(f[idx:idx2+1]*interp,w[idx:idx2+1])#area in the band we have for color calculation
        area=model.bandmag(bands[known],zpsys,currentPhase)

        #area=bandMag(sedfile,currentPhase,bands[known],zpsys)#magnitude in the band we have for color calculation
        val=int(math.ceil(wave2[0]/wavestep))*wavestep
        val2=int(math.floor(wave2[-1]/wavestep))*wavestep
        wave=arange(val,val2+1,wavestep)
        trans=interpFunc2(wave)
        return(wave,trans,area)
    UV=False
    IR=False
    for i in range( len(dlist) ) :
        d,w,f = dlist[i],wlist[i],flist[i]
        #if not bandsDone:
            #f=f/sncosmo.constants.HC_ERG_AA #flux is now in photons/s/cm^2

        wavestep = w[1] - w[0]
        if bands[blue].wave_eff<=_UVrightBound:
            bWave,bTrans,rArea=_extrap_helper(rWave,rInterpFunc,bWave,bInterpFunc,red,d[0])
            wnew,fnew=_extrapolate_uv(blue,rArea,colorData[i],bTrans,bWave,f,w,niter,log,i,bands,zpsys)
            UV=True
        elif bands[red].wave_eff>=_IRleftBound:
            rWave,rTrans,bArea=_extrap_helper(bWave,bInterpFunc,rWave,rInterpFunc,blue,d[0])
            wnew,fnew=_extrapolate_ir(red,bArea,colorData[i],rTrans,rWave,d,f,w,niter,log,i,IR,bands,zpsys,model,bandsDone)
            IR=True
        else:
            raise RuntimeError("You supplied a color that does not support extrapolation to the IR or UV!")
        for j in range( len( wnew ) ) :
            fout.write("%5.1f  %10i  %12.7e \n"%( d[0], wnew[j], fnew[j] ))
    fout.close() 
    log.close()
    return( UV,IR )

def _boundUVsed(sedfile):
    dlist,wlist,flist = _getsed(sedfile)
    fout = open( sedfile, 'w' )
    for i in range(len(dlist)):
        d,w,f = dlist[i],wlist[i],flist[i]
        if w[0]>_UVleftBound:
            wavestep=w[1]-w[0]
            (a,b,rval,pval,stderr)=stats.linregress(append(_UVleftBound,w[0]),append(0,f[0]))
            Nstep = len( arange( _UVleftBound, w[0],  wavestep ) )
            wext =  sorted( [ _UVleftBound + (j)*wavestep for j in range(Nstep) ] )
            fext = array( [ max( 0, a * wave + b ) for wave in wext ] )
            wnew=append(wext,w)
            fnew=append(fext,f)
        else:
            wnew=w
            fnew=f
        for j in range( len( wnew ) ) :
            fout.write("%5.1f  %10i  %12.7e \n"%( d[0], wnew[j], fnew[j] ))
    fout.close()

def _boundIRsed(sedfile):
    dlist,wlist,flist = _getsed(sedfile)
    fout = open( sedfile, 'w' )
    for i in range(len(dlist)):
        d,w,f = dlist[i],wlist[i],flist[i]
        if w[-1]<_IRrightBound:
            wavestep=w[1]-w[0]
            (a,b,rval,pval,stderr)=stats.linregress(append(w[-1],_IRrightBound),append(f[-1],0))
            Nstep = len( arange( w[-1], _IRrightBound,  wavestep ) )
            wext =  sorted( [ w[-1] + (j+1)*wavestep for j in range(Nstep) ] )
            fext = array( [ max( 0, a * wave + b ) for wave in wext ] )
            wnew=append(w,wext)
            fnew=append(f,fext)
        else:
            wnew=w
            fnew=f
        for j in range( len( wnew ) ) :
            fout.write("%5.1f  %10i  %12.7e \n"%( d[0], wnew[j], fnew[j] ))
    fout.close()



def bandMag(filename,currentPhase,band,zpsys,rescale=False):
    sed=_createSED(filename,rescale=rescale)
    model=sncosmo.Model(sed)
    return(model.bandmag(band,zpsys,currentPhase))



def extendNon1a(colorTable,bandDict=_filters,colors=None,zpsys='AB',sedlist=None,showplots=None,verbose=True):
    """
    Function called in main which runs the extrapolation algorithm.
    :param sedlist: list of files to analyze (or none if all in current directory)
    :param fVJ: input V-J
    :param fVH: input V-H
    :param fVK: input V-K
    :param showplots: None if you don't want plotting, otherwise it's 'all' if you want to plot all days or a single day (i.e. 3)
    """
    import pickle
    colorTable=_standardize(colorTable)
    print('Running Bayesian Information Criterion...')
    temp=snsedextend.BIC.run()
    #with open('tempBIC.txt','wb') as handle:
    #    pickle.dump(temp,handle)
    ###### for debugging#####
    #with open('tempBIC.txt','rb') as handle:
    #    temp=pickle.loads(handle.read())


    #bandDict=dict((k.upper(),v) for k,v in bandDict.iteritems())
    if not isinstance(colorTable,Table):
        raise RuntimeError('Colors argument must be an astropy table.')

    for band in bandDict:
        if not isinstance(bandDict[band],sncosmo.Bandpass):
            bandDict=_bandCheck(bandDict,band)
    if not colors:
        print("No extrapolation colors defined, assuming: U-B, r-H, r-J, r-K")
        colors=['U-B','r-J','r-H','r-K']
    else:
        tempColors=[x for x in colors if 'U-' in x]+[x for x in colors if '-J' in x]+[x for x in colors if '-H' in x]+[x for x in colors if '-K' in x]
        if len(tempColors)>4:
            raise RuntimeError("Only can do 4 colors!")


    bands=append([col[0] for col in colors],[col[-1] for col in colors])
    for band in _filters.keys():
        if band not in bandDict.keys() and band in bands:
            bandDict[band]=sncosmo.get_bandpass(_filters[band])
    if not sedlist:
        sedlist = glob.glob(os.path.join(sndataroot,"snsed","NON1A","*.SED"))
    else:
        sedlist=[sedlist] if not isinstance(sedlist,(tuple,list)) else sedlist
        sedlist=[os.path.join(sndataroot,"snsed","NON1A",sed) for sed in sedlist]
    for sedfile in sedlist:
        newsedfile=os.path.join('/Users','jpierel','rodney','snsedextend','SEDs','typeII',os.path.basename(sedfile))
        #newsedfile=os.path.basename(sedfile)
        if verbose:
            print("EXTRAPOLATING %s"%newsedfile)
        once=False
        boundUV=False
        boundIR=False
        bandsDone=[]
        for color in colors:
            if verbose:
                print('     Running %s extrapolation.'%color)
            if color[0] not in bandDict.keys():
                raise RuntimeError('Band "%s" defined in color "%s", but band is not defined.')
            if color[-1] not in bandDict.keys():
                raise RuntimeError('Band "%s" defined in color "%s", but band is not defined.')
            try:
                tempTime=array(temp[color[-1]]['x'])
                tempColor=array(temp[color[-1]]['500'])
            except:
                tempTime=array(temp['uv']['x'])
                tempColor=array(temp['uv']['500'])

            getExtremes(tempTime,tempColor,colorTable,color)
            #blueZP=_getZP(bandDict[color[0]],zpsys)
            #redZP=_getZP(bandDict[color[-1]],zpsys)
            tempMask=colorTable[color].mask
            #colorTable[color]=10**(-.4*(colorTable[color]-(blueZP-redZP)))
            #tempColor=10**(-.4*(tempColor-(blueZP-redZP)))
            colorTable[color].mask=tempMask
            if once:
                sedfile=newsedfile
            tempTable=colorTable[~colorTable[color].mask]
            UV,IR=_extrapolatesed(sedfile, newsedfile,color,tempTable,tempTime,tempColor, bandDict,zpsys,bandsDone,niter=50)
            if UV:
                boundUV=True
                bandsDone.append(color[0])
            if IR:
                boundIR=True
                bandsDone.append(color[-1])
            once=True

        #once=False

        if showplots:
            plotsed(newsedfile,day=showplots)
        if boundUV:
            _boundUVsed(newsedfile)
        if boundIR:
            _boundIRsed(newsedfile)
        if verbose:
            print("     Done with %s.\a\a\a"%os.path.basename(sedfile))


def getExtremes(time,curve,original,color):
    def getErrors(x):
        err=0
        N = len(np.nonzero(x)[0])
        for i in range(len(x)):
            err=err+(1/(x[i])**2)
        if N>1:
            samplecorrection = np.float((N-1))/np.float(N)
        else:
            samplecorrection=1
        return((1/(err)**.5)/np.sqrt(samplecorrection))

    error=getErrors(np.array(original[color[0]+color[-1]+'_err'])[np.array(original[color[0]+color[-1]+'_err'])!=0])

    blue=curve-9*error
    red=curve+9*error
    plt.plot(time,curve,color='k')
    plt.plot(time,blue,color='b')
    plt.plot(time,red,color='r')
    plt.show()
    sys.exit()

def bandRegister(wave,trans,band,bandName):
    sncosmo.registry.register(sncosmo.Bandpass(wave,trans,name=bandName))
    _filters[bandName]=sncosmo.get_bandpass(bandName)




def curveToColor(filename,colors,bandFit=None,snType='II',bandDict=_filters,zpsys='AB',method='minuit',
                 model=None, params=None, bounds=None, ignore=None, constants=None,dust=None,effect_names=None,effect_frames=None,singleBand=False,verbose=True):
    bands=append([col[0] for col in colors],[col[-1] for col in colors])
    for band in _filters:
        if band not in bandDict.keys() and band in bands:
            bandDict[band]=sncosmo.get_bandpass(_filters[band])
    if not isinstance(colors,(tuple,list)):
        colors=[colors]
    zpMag=sncosmo.get_magsystem(zpsys)

    curve=_standardize(sncosmo.read_lc(filename))
    if _get_default_prop_name('zpsys') not in curve.colnames:
        curve[_get_default_prop_name('zpsys')]=zpsys
    colorTable=Table(masked=True)
    colorTable.add_column(Column(data=[],name=_get_default_prop_name('time')))
    color_bands=['B','V','R','g','r']
    for band in bandDict:
        if not isinstance(bandDict[band],sncosmo.Bandpass):
            bandDict=_bandCheck(bandDict,band)
    t0=None
    if verbose:
        print('Getting best fit for: '+','.join(colors))
    for color in colors:
        if bandDict[color[0]].wave_eff<_UVrightBound:
            if not bandFit:
                bandFit=color[-1]
            if singleBand:
                color_bands=[color[-1]]
            blue=curve[curve[_get_default_prop_name('band')]==color[0]]
            red=curve[[x in color_bands for x in curve[_get_default_prop_name('band')]]]
        else:
            if not bandFit:
                bandFit=color[0]
            if singleBand:
                color_bands=[color[0]]
            blue=curve[[x in color_bands for x in curve[_get_default_prop_name('band')]]]
            red=curve[curve[_get_default_prop_name('band')]==color[-1]]
        if len(blue)==0 or len(red)==0:
            if verbose:
                print('Asked for coor %s but missing necessary band(s)')
            continue

        btemp=[bandDict[blue[_get_default_prop_name('band')][i]].name for i in range(len(blue))]
        rtemp=[bandDict[red[_get_default_prop_name('band')][i]].name for i in range(len(red))]
        blue.remove_column(_get_default_prop_name('band'))
        blue[_get_default_prop_name('band')]=btemp
        red.remove_column(_get_default_prop_name('band'))
        red[_get_default_prop_name('band')]=rtemp
        if _get_default_prop_name('zp') not in blue.colnames:
            blue[_get_default_prop_name('zp')]=[zpMag.band_flux_to_mag(1,blue[_get_default_prop_name('band')][i]) for i in range(len(blue))]
        if _get_default_prop_name('zp') not in red.colnames:
            red[_get_default_prop_name('zp')]=[zpMag.band_flux_to_mag(1,red[_get_default_prop_name('band')][i]) for i in range(len(red))]
        if _get_default_prop_name('flux') not in blue.colnames:
            blue=_mag_to_flux(blue,bandDict,zpsys)
        if _get_default_prop_name('flux') not in red.colnames:
            red=_mag_to_flux(red,bandDict,zpsys)

        if not t0:
            if not model:
                if verbose:
                    print('No model provided, running series of models.')
                #print(snType)
                mod,types=np.loadtxt('hicken/models.ref',dtype='str',unpack=True)
                modDict={mod[i]:types[i] for i in range(len(mod))}
                if snType!='1a':
                    mods = [x for x in sncosmo.models._SOURCES._loaders.keys() if x[0] in modDict.keys() and modDict[x[0]][:len(snType)]==snType]
                elif snType=='1a':
                    mods = [x for x in sncosmo.models._SOURCES._loaders.keys() if 'salt2' in x[0]]

                '''
                mods={mod[i]:types[i] for i in range(len(mod))}
                mods=[x for x in mods.keys() if mods[x]=='IIn']
                '''
                mods = {x[0] if isinstance(x,(tuple,list)) else x for x in mods}
                p = Pool(processes=multiprocessing.cpu_count())
                fits=[]

                if len(blue)>len(red) or bandFit==color[0]:
                    for x in p.imap_unordered(_snFitWrap,[(x,blue,method,params,bounds,ignore,constants,dust,effect_names,effect_frames) for x in mods]):
                        fits.append(x)
                    p.close()
                    fitted=blue
                    notFitted=red
                    fit=color[0]
                elif len(blue)<len(red) or bandFit==color[-1]:
                    for x in p.imap_unordered(_snFitWrap,[(x,red,method,params,bounds,ignore,constants,dust,effect_names,effect_frames) for x in mods]):
                        fits.append(x)
                    p.close()
                    fitted=red
                    notFitted=blue
                    fit=color[-1]
                else:
                    raise RuntimeError('Neither band "%s" nor band "%s" has more points, and you have not specified which to fit.'%(color[0],color[-1]))
                bestChisq=inf
                for f in fits:
                    if f:
                        res,mod=f
                        if res.chisq <bestChisq:
                            bestChisq=res.chisq
                            bestFit=mod
                            bestRes=res
                if verbose:
                    print('Best fit model is "%s", with a Chi-squared of %f'%(bestFit._source.name,bestChisq))
            elif len(blue)>len(red) or bandFit==color[0]:
                bestRes,bestFit=_snFit((model,blue,method,params,bounds,ignore,constants,dust,effect_names,effect_frames))
                fitted=blue
                notFitted=red
                fit=color[0]
                if verbose:
                    print('The model you chose (%s) completed with a Chi-squared of %f'%(model,bestRes.chisq))
            elif len(blue)<len(red) or bandFit==color[-1]:
                bestRes,bestFit=_snFit((model,red,method,params,bounds,ignore,constants,dust,effect_names,effect_frames))
                fitted=red
                notFitted=blue
                fit=color[-1]
                if verbose:
                    print('The model you chose (%s) completed with a Chi-squared of %f'%(model,bestRes.chisq))
            else:
                raise RuntimeError('Neither band "%s" nor band "%s" has more points, and you have not specified which to fit.'%(color[0],color[-1]))

            #t0=bestRes.parameters[bestRes.param_names.index('t0')]
            t0=_getBandMaxTime(bestFit,fitted,bandDict,'B',zpMag.band_flux_to_mag(1,bandDict['B']),zpsys)
            if len(t0)==1:
                t0=t0[0]
            else:
                raise RuntimeError('Multiple global maxima in best fit.')
        else:
            if len(blue)>len(red) or bandFit==color[0]:
                fitted=blue
                notFitted=red
                fit=color[0]
            elif len(blue)<len(red) or bandFit==color[-1]:
                fitted=red
                notFitted=blue
                fit=color[-1]
            else:
                raise RuntimeError('Neither band "%s" nor band "%s" has more points, and you have not specified which to fit.'%(color[0],color[-1]))
        tGrid,bestMag=_snmodel_to_mag(bestFit,fitted,zpsys,bandDict[fit])
        #corrFlux=_ccm_unred()zpMag.band_flux_to_mag(1,bandDict[fit]),
        ugrid,UMagErr,lgrid,LMagErr=_getErrorFromModel((bestFit._source.name,fitted,method,params,bounds,ignore,constants,dust,effect_names,effect_frames),zpsys,bandDict[fit])
        tempTable=Table([tGrid-t0,bestMag,bestMag*.1],names=(_get_default_prop_name('time'),_get_default_prop_name('mag'),_get_default_prop_name('magerr')))
        interpFunc=scint.interp1d(array(tempTable[_get_default_prop_name('time')]),array(tempTable[_get_default_prop_name('mag')]))
        minterp=interpFunc(array(notFitted[_get_default_prop_name('time')]-t0))
        interpFunc=scint.interp1d(ugrid-t0,UMagErr)
        uinterp=interpFunc(array(notFitted[_get_default_prop_name('time')]-t0))
        interpFunc=scint.interp1d(lgrid-t0,LMagErr)
        linterp=interpFunc(array(notFitted[_get_default_prop_name('time')]-t0))
        magerr=mean([minterp-uinterp,linterp-minterp],axis=0)

        for i in range(len(minterp)):
            colorTable.add_row(append(notFitted[_get_default_prop_name('time')][i]-t0,[1 for j in range(len(colorTable.colnames)-1)]),mask=[True if j>0 else False for j in range(len(colorTable.colnames))])
        if fit==color[0]:
            colorTable[color]=MaskedColumn(append([1 for j in range(len(colorTable)-len(minterp))],minterp-array(notFitted[_get_default_prop_name('mag')])),mask=[True if j<(len(colorTable)-len(minterp)) else False for j in range(len(colorTable))])
            #print(bestRes.parameters[bestRes.param_names.index('t0')])
            #sncosmo.plot_lc(blue,model=bestFit,errors=bestRes.errors)
        else:
            colorTable[color]=MaskedColumn(append([1 for j in range(len(colorTable)-len(minterp))],array(notFitted[_get_default_prop_name('mag')])-minterp),mask=[True if j<(len(colorTable)-len(minterp)) else False for j in range(len(colorTable))])
            #print(bestRes.parameters[bestRes.param_names.index('t0')])
            #sncosmo.plot_lc(red,model=bestFit,errors=bestRes.errors)
        colorTable[color[0]+color[-1]+'_err']=MaskedColumn(append([1 for j in range(len(colorTable)-len(magerr))],magerr+array(notFitted[_get_default_prop_name('magerr')])),mask=[True if j<(len(colorTable)-len(magerr)) else False for j in range(len(colorTable))])
    #plt.savefig(filename[:-3]+'pdf',format='pdf')
        #sncosmo.write_lc(colorTable,os.path.join('modjaz','type'+snType[:2],'tables','uncorr_'+os.path.basename(filename)))
        for name in bestFit.effect_names:
            magCorr=_unredden(color,bandDict,bestRes.parameters[bestRes.param_names.index(name+'ebv')],bestRes.parameters[bestRes.param_names.index(name+'r_v')])
            colorTable[color]-=magCorr
        #sncosmo.write_lc(colorTable,os.path.join('modjaz','type'+snType[:2],'tables','corr_'+os.path.basename(filename)))
    colorTable.sort(_get_default_prop_name('time'))
    #plt.show()

    return(colorTable)

def _getErrorFromModel(args,zpsys,band):
    model,curve,method,params,bounds,ignore,constants,dust,effect_names,effect_frames=args
    curve[_get_default_prop_name('flux')]=curve[_get_default_prop_name('flux')]+curve[_get_default_prop_name('fluxerr')]
    res,fit=_snFit((model,curve,method,params,bounds,ignore,constants,dust,effect_names,effect_frames))
    ugrid,umag=_snmodel_to_mag(fit,curve,zpsys,band)
    curve[_get_default_prop_name('flux')]=curve[_get_default_prop_name('flux')]-2*curve[_get_default_prop_name('fluxerr')]
    res,fit=_snFit((model,curve,method,params,bounds,ignore,constants,dust,effect_names,effect_frames))
    lgrid,lmag=_snmodel_to_mag(fit,curve,zpsys,band)
    return (ugrid,umag,lgrid,lmag)


def _getBandMaxTime(model,table,bands,band,zp,zpsys):
    tgrid,mflux=_snmodel_to_flux(model,table,zp,zpsys,bands[band])
    #idx,val=_find_nearest(tgrid,min(tgrid)+30)
    return(tgrid[where(mflux==max(mflux))])

def _bandCheck(bandDict,band):
    try:
        bandDict[band]=sncosmo.get_bandpass(bandDict[band])
    except:
        try:
            wave,trans=np.loadtxt(_findFile(bandDict[band]),unpack=True)
            bandDict[band]=bandRegister(wave,trans,band,os.path.basename(bandDict[band]))
        except:
            raise RuntimeError('Band "%s" listed in bandDict but not in sncosmo registry and file not found.'%band)
    return(bandDict)

def _snFitWrap(args):
    try:
        return(_snFit(args))
    except RuntimeError:
        return(None)

def _snFit(args):
    warnings.simplefilter('ignore')
    sn_func={'minuit': sncosmo.fit_lc, 'mcmc': sncosmo.mcmc_lc, 'nest': sncosmo.nest_lc}
    dust_dict={'SFD98Map':sncosmo.SFD98Map,'CCM89Dust':sncosmo.CCM89Dust,'OD94Dust':sncosmo.OD94Dust,'F99Dust':sncosmo.F99Dust}
    model,curve,method,params,bounds,ignore,constants,dust,effect_names,effect_frames=args
    if dust:
        dust=dust_dict[dust]()
    else:
        dust=[]
    bounds=bounds if bounds else {}
    ignore=ignore if ignore else []
    effects=[dust for i in range(len(effect_names))] if effect_names else []
    effect_names=effect_names if effect_names else []
    effect_frames=effect_frames if effect_frames else []
    if not isinstance(effect_names,(list,tuple)):
        effects=[effect_names]
    if not isinstance(effect_frames,(list,tuple)):
        effects=[effect_frames]
    model=sncosmo.Model(source=model,effects=effects,effect_names=effect_names,effect_frames=effect_frames)
    params=params if params else [x for x in model.param_names]
    no_bound = {x for x in params if x in _needs_bounds and x not in bounds.keys() and x not in constants.keys()}
    if no_bound:
        params=list(set(params)-no_bound)
    params= [x for x in params if x not in ignore and x not in constants.keys()]
    if constants:
        constants = {x:constants[x] for x in constants.keys() if x in model.param_names}
        model.set(**constants)
    res,fit=sn_func[method](curve, model, params, bounds=bounds,verbose=False)
    return((res,fit))

"""
mods = [x for x in sncosmo.models._SOURCES._loaders.keys() if 'snana' in x[0] or 'salt2' in x[0]]
    mods = {x[0] if isinstance(x,(tuple,list)) else x for x in mods}"""

def _mag_to_flux(table,bandDict,zpsys):
    ms=sncosmo.get_magsystem(zpsys)
    table[_get_default_prop_name('flux')]=np.asarray(map(lambda x,y: ms.band_mag_to_flux(x,y)*sncosmo.constants.HC_ERG_AA,np.asarray(table[_get_default_prop_name('mag')]),
                                                         table[_get_default_prop_name('band')]))
    table[_get_default_prop_name('fluxerr')] = np.asarray(
        map(lambda x, y: x * y / (2.5 * np.log10(np.e)), table[_get_default_prop_name('magerr')],
            table[_get_default_prop_name('flux')]))
    return table

def _flux_to_mag(table,bandDict,zpsys):
    ms=sncosmo.get_magsystem(zpsys)
    table[_get_default_prop_name('mag')] = np.asarray(map(lambda x, y: ms.band_flux_to_mag(x/sncosmo.constants.HC_ERG_AA,y), table[_get_default_prop_name('flux')],
                                                          bandDict[table[_get_default_prop_name('band')]]))
    table[_get_default_prop_name('magerr')] = np.asarray(map(lambda x, y: 2.5 * np.log10(np.e) * y / x, table[_get_default_prop_name('flux')],
                                                             table[_get_default_prop_name('fluxerr')]))
    return table

def _snmodel_to_mag(model,table,zpsys,band):
    warnings.simplefilter("ignore")
    tmin = []
    tmax = []
    tmin.append(np.min(table[_get_default_prop_name('time')]) - 10)
    tmax.append(np.max(table[_get_default_prop_name('time')]) + 10)
    tmin.append(model.mintime())
    tmax.append(model.maxtime())
    tmin = min(tmin)
    tmax = max(tmax)
    tgrid = np.linspace(tmin, tmax, int(tmax - tmin) + 1)
    mMag = model.bandmag(band, zpsys,tgrid)
    return(tgrid,mMag)

def _snmodel_to_flux(model,table,zp,zpsys,band):
    warnings.simplefilter("ignore")
    tmin = []
    tmax = []
    tmin.append(np.min(table[_get_default_prop_name('time')]) - 10)
    tmax.append(np.max(table[_get_default_prop_name('time')]) + 10)
    tmin.append(model.mintime())
    tmax.append(model.maxtime())
    tmin = min(tmin)
    tmax = max(tmax)
    tgrid = np.linspace(tmin, tmax, int(tmax - tmin) + 1)
    mflux = model.bandflux(band, tgrid, zp=zp, zpsys=zpsys)
    return(tgrid,mflux)


def _getReddeningCoeff(band):
    wave=[10000/2.86,10000/2.78,10000/2.44,10000/2.27,10000/2.13,10000/1.43,10000/1.11,10000/.8,10000/.63,10000/.46,10000/.29]
    a=[.913,0.958,.98,1.0,.974,0.855,0.661,0.421,0.225,0.148,0.043]
    b=[2.14,1.898,1.284,1,.803,-.309,-.555,-.458,-.243,-.099,.159]
    aInterpFunc=scint.interp1d(wave,a)
    bInterpFunc=scint.interp1d(wave,b)
    return(aInterpFunc(band.wave_eff),bInterpFunc(band.wave_eff))



def _unredden(color,bands,ebv,r_v):
    A_v=r_v*ebv
    blue=bands[color[0]]
    red=bands[color[-1]]
    if not isinstance(blue,sncosmo.Bandpass):
        blue=sncosmo.get_bandpass(blue)
    if not isinstance(red,sncosmo.Bandpass):
        red=sncosmo.get_bandpass(red)
    if color[0]=='V':
        a,b=_getReddeningCoeff(red)
        A_blue=A_v
        A_red=(a+b/r_v)*A_v
    elif color[-1]=='V':
        a,b=_getReddeningCoeff(blue)
        A_blue=(a+b/r_v)*A_v
        A_red=A_v
    else:
        a_blue,b_blue=_getReddeningCoeff(blue)
        a_red,b_red=_getReddeningCoeff(red)
        A_blue=(a_blue+b_blue/r_v)*A_v
        A_red=(a_red+b_red/r_v)*A_v
    return(A_blue-A_red)
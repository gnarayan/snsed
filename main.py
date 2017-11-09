import snsedextend
from astropy.table import Table
import matplotlib.pyplot as plt
import numpy as np
import glob,os,sys
from astropy.table import vstack
from astropy.io import ascii
import sncosmo

dir='hicken'
type=['II','IIP']
filelist=[os.path.basename(file) for file in glob.glob(os.path.join(dir,'*clipped.dat'))]
#filelist=['lc_2002bx.dat','lc_2006ca.dat','lc_2006cd.dat','lc_2006it.dat','lc_2007aa.dat','lc_2007av.dat','lc_2008bj.dat','lc_2008bn','lc_2008in.dat','lc_2009ay.dat','lc_2009kn.dat',]
#IIn=['lc_2008ip.dat','lc_2010bq.dat']
sn,redshift=np.loadtxt(os.path.join(dir,'redshift.ref'),dtype='str',unpack=True)
redshift={sn[i]:redshift[i] for i in range(len(sn))}
sn,colors=np.loadtxt(os.path.join(dir,'color.ref'),dtype='str',unpack=True)
colors={sn[i]:colors[i] for i in range(len(sn))}
sn,dusts=np.loadtxt(os.path.join(dir,'dust.ref'),dtype='str',unpack=True)
dust={sn[i]:dusts[i] for i in range(len(sn))}
sn,types=np.loadtxt(os.path.join(dir,'type.ref'),dtype='str',unpack=True)
typ={sn[i]:types[i] for i in range(len(sn))}
sne,peaks=np.loadtxt(os.path.join(dir,'timeOfPeak.dat'),dtype='str',unpack=True)
peaks={sne[i]:float(peaks[i]) for i in range(len(sne))}

typeColors=None

for k in colors:
    if len(colors[k])>3:
        colors[k]=colors[k].split(',')
    elif not isinstance(colors[k],(list,tuple)):
        colors[k]=[colors[k]]
for i in range(len(filelist)):
    if typ[filelist[i][:-12]] in type and filelist[i] not in ['lc_2005kl_clipped.dat','lc_2005az_clipped.dat','lc_2008aq_clipped.dat']:#['lc_2006fo_clipped.dat','lc_2006jc_clipped.dat','lc_2004ao_clipped.dat','lc_2007D_clipped.dat','lc_2005bf_clipped.dat','lc_2005nb_clipped.dat','lc_2006ld_clipped.dat']:

        print(filelist[i])
        colorTable=snsedextend.curveToColor(os.path.join(dir,filelist[i]),colors[filelist[i][:-12]],snType=typ[filelist[i][:-12]],zpsys='Vega',
                                            bounds={'hostebv':(-1,1),'t0':(peaks[filelist[i][:-12]]-5,peaks[filelist[i][:-12]]+5)},
                                            constants={'z':redshift[filelist[i][:-12]],'hostr_v':3.1,'mwr_v':3.1,'mwebv':dust[filelist[i][:-12]]},
                                            dust='CCM89Dust',effect_frames=['rest','obs'],effect_names=['host','mw'])
        '''
        for color in colors[filelist[i][:-12]]:
            fig=plt.figure()
            ax=plt.gca()
            ax.errorbar(np.array(colorTable['time'][colorTable[color].mask==False]),np.array(colorTable[color][colorTable[color].mask==False]),
                        yerr=np.array(colorTable[color.replace('-','')+'_err'][colorTable[color].mask==False]),fmt='x')
            ax.set_title(filelist[i][3:])
            ax.invert_yaxis()
            plt.title(color+' Color')
            fig.text(0.5, 0.01, 'Time (Since Peak)', ha='center')
            fig.text(0.01, 0.5, 'Color Magnitude (Vega)', va='center', rotation='vertical')
            plt.savefig(os.path.join(dir,"type"+type[0],"plots",filelist[i][:-12]+"_"+color[0]+color[-1]+".pdf"),format='pdf')
            plt.close()
        '''
        if typeColors:
            typeColors=vstack([typeColors,colorTable])
        else:
            typeColors=colorTable

print(typeColors)
seds={
    'Ic':['SDSS-013195.SED','SDSS-014475.SED','SDSS-015475.SED','SDSS-017548.SED'],
    'Ib':['SDSS-000020.SED','SDSS-002744.SED','SDSS-014492.SED','SDSS-019323.SED'],
    'II':['SDSS-003818.SED']
}
#snsedextend.extendNon1a(typeColors,sedlist=seds[type[0]],verbose=True)
    #snsedextend.extendNon1a(colorTable,sedlist='SDSS-0 13449.SED',verbose=True)
typeColors.sort('time')
#sncosmo.write_lc(typeColors,'lcs_clipped2/tables/allColors.dat')
ascii.write(typeColors,os.path.join(dir,'type'+type[0],'tables','all'+type[0]+'Colors.dat'))
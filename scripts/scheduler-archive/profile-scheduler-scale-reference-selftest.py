#!/usr/bin/env python3
"""Offline Q4_K layout and intermediate-scale arithmetic checks; no qualification."""
import argparse
import array
import json
import math
from pathlib import Path
import struct
import subprocess
import tempfile


def main():
    p=argparse.ArgumentParser();p.add_argument('--binary',type=Path,required=True);a=p.parse_args()
    f32=lambda x:struct.unpack('<f',struct.pack('<f',x))[0]
    half=lambda x:struct.unpack('<e',struct.pack('<e',x))[0]
    with tempfile.TemporaryDirectory(prefix='skippy-scale-') as tmp:
        root=Path(tmp);name=b'blk.0.attn_gate.weight'
        header=b'GGUF'+struct.pack('<IQQ',3,1,0)+struct.pack('<Q',len(name))+name
        header+=struct.pack('<IQQIQ',2,2560,4096,12,0);header+=bytes((-len(header))%32)
        x=[((k%17)-8)/8 for k in range(2560)]
        expected={k:[] for k in ('reference','scalehalf','scalef32','scaleftz')};rows=[]
        for r in range(4):
            d=struct.unpack('<e',struct.pack('<H',(0x401,0x40f,0x2401,0x37ff)[r]))[0];m=half(2**-15)
            scales=bytes((13+i*19)%256 for i in range(12));q=bytes((i*31+r*7)%256 for i in range(128))
            rows.append((struct.pack('<ee',d,m)+scales+q)*10)
            values={key:[] for key in expected}
            for k in range(2560):
                sub=k%256//32
                sc=(scales[sub]&63) if sub<4 else (scales[sub+4]&15)|((scales[sub-4]>>6)<<4)
                mn=(scales[sub+4]&63) if sub<4 else (scales[sub+4]>>4)|((scales[sub]>>6)<<4)
                packed=q[k%256//64*32+k%32];nibble=(packed>>4) if sub%2 else packed&15
                values['reference'].append(f32(f32(d*sc)*nibble-f32(m*mn))*x[k])
                for key in ('scalehalf','scalef32','scaleftz'):
                    scale=d/16 if sub%2 else d
                    if sub%2 and key!='scalef32':scale=half(scale)
                    if sub%2 and key=='scaleftz' and abs(scale)<2**-14:scale=0
                    quant=nibble*16 if sub%2 else nibble
                    values[key].append(f32(f32(scale*sc)*quant-f32(m*mn))*x[k])
            for key in expected:expected[key].append(math.fsum(values[key]))
        with (root/'model.gguf').open('wb') as f:
            f.write(header)
            for row in range(4096):f.write(rows[row%4])
        (root/'input.f32').write_bytes(array.array('f',x).tobytes())
        y=[expected['reference'][r%4] for r in range(4096)]
        (root/'output.f32').write_bytes(array.array('f',y).tobytes())
        (root/'cases.json').write_text(json.dumps([{'label':'synthetic','input':str(root/'input.f32'),'output':str(root/'output.f32')}]))
        subprocess.run([str(a.binary.resolve()),str(root/'model.gguf'),str(root/'cases.json'),str(root/'result')],check=True)
        for key in expected:
            v=array.array('d');v.frombytes((root/'result'/('synthetic-'+key+'.f64')).read_bytes())
            assert all(abs(z-expected[key][r%4])<1e-12 for r,z in enumerate(v)),key
        assert expected['reference']==expected['scalef32']
        assert expected['scalehalf']!=expected['reference'] and expected['scaleftz']!=expected['scalehalf']
        print('PASS: 4096 Q4_K rows, all subblocks/nibbles, normal/subnormal scale controls match independent Python arithmetic. Not hardware qualification.')


if __name__=='__main__':main()

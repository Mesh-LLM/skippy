#!/usr/bin/env python3
"""Check GGUF row addressing and FP64 arithmetic against exact synthetic values."""
import argparse
import array
import json
from pathlib import Path
import struct
import subprocess
import tempfile


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--binary',type=Path,required=True);args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='skippy-gate-reference-') as temp:
        root=Path(temp);name=b'blk.0.attn_gate.weight'
        header=b'GGUF'+struct.pack('<IQQ',3,1,0)+struct.pack('<Q',len(name))+name
        header+=struct.pack('<IQQIQ',2,2560,4096,0,0);header+=bytes((-len(header))%32)
        expected=[]
        with (root/'test.gguf').open('wb') as file:
            file.write(header)
            for row in range(4096):
                w=array.array('f',[0.0])*2560;w[0]=(row%128)/64;w[1]=0.125
                file.write(w.tobytes());expected.append(2*w[0]-w[1])
        x=array.array('f',[0.0])*2560;x[0]=2;x[1]=-1;(root/'input.f32').write_bytes(x.tobytes())
        (root/'exact.f32').write_bytes(array.array('f',expected).tobytes());wrong=expected[:];wrong[5]+=0.5
        (root/'wrong.f32').write_bytes(array.array('f',wrong).tobytes())
        cases=[{'label':label,'input':str(root/'input.f32'),'output':str(root/(label+'.f32'))} for label in ('exact','wrong')]
        xr=array.array('f',x);xr[0]=2.0003;(root/'rounded-input.f32').write_bytes(xr.tobytes())
        yr=[(row%128)/64*xr[0]-0.125 for row in range(4096)]
        (root/'rounded-output.f32').write_bytes(array.array('f',yr).tobytes())
        cases.append({'label':'rounded','input':str(root/'rounded-input.f32'),'output':str(root/'rounded-output.f32')})
        (root/'cases.json').write_text(json.dumps(cases))
        subprocess.run([str(args.binary.resolve()),str(root/'test.gguf'),str(root/'cases.json'),str(root/'output')],check=True,stdout=subprocess.DEVNULL)
        report=json.loads((root/'output/reference.json').read_text());a,b=report['cases'][:2]
        assert a['max_abs_error']==a['max_plain_vs_compensated']==a['outside_generic_fp32_bound']==0
        assert b['max_abs_error']==0.5 and b['worst_row']==5 and b['outside_generic_fp32_bound']==1
        reference=array.array('d');reference.frombytes((root/'output/exact-reference.f64').read_bytes())
        assert list(reference)==expected
        half_input=struct.unpack('<e',struct.pack('<e',xr[0]))[0]
        expected_half=[(row%128)/64*half_input-0.125 for row in range(4096)]
        for suffix in ('input16','both16'):
            values=array.array('d');values.frombytes((root/'output'/('rounded-'+suffix+'.f64')).read_bytes())
            assert list(values)==expected_half
        assert expected_half!=yr
        print('Synthetic4096-row GGUF exact arithmetic passed; deliberate0.5 error atrow5 detected. Not model/hardware qualification.')


if __name__=='__main__':main()

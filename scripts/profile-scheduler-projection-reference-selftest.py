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
    with tempfile.TemporaryDirectory(prefix='skippy-projection-reference-') as temp:
        root=Path(temp);name=b'blk.0.attn_qkv.weight'
        header=b'GGUF'+struct.pack('<IQQ',3,1,0)+struct.pack('<Q',len(name))+name
        header+=struct.pack('<IQQIQ',2,2560,8192,0,0);header+=bytes((-len(header))%32)
        expected=[]
        with (root/'test.gguf').open('wb') as file:
            file.write(header)
            for row in range(8192):
                w=array.array('f',[0.0])*2560;w[0]=(row%128)/64;w[1]=0.125
                file.write(w.tobytes());expected.append(2*w[0]-w[1])
        x=array.array('f',[0.0])*2560;x[0]=2;x[1]=-1;(root/'input.f32').write_bytes(x.tobytes())
        (root/'exact.f32').write_bytes(array.array('f',expected).tobytes());wrong=expected[:];wrong[5]+=0.5
        (root/'wrong.f32').write_bytes(array.array('f',wrong).tobytes())
        cases=[{'label':label,'input':str(root/'input.f32'),'output':str(root/(label+'.f32'))} for label in ('exact','wrong')]
        (root/'cases.json').write_text(json.dumps(cases))
        subprocess.run([str(args.binary.resolve()),str(root/'test.gguf'),str(root/'cases.json'),str(root/'output')],check=True,stdout=subprocess.DEVNULL)
        report=json.loads((root/'output/reference.json').read_text());a,b=report['cases']
        assert a['max_abs_error']==a['max_plain_vs_compensated']==a['outside_generic_fp32_bound']==0
        assert b['max_abs_error']==0.5 and b['worst_row']==5 and b['outside_generic_fp32_bound']==1
        reference=array.array('d');reference.frombytes((root/'output/exact-reference.f64').read_bytes())
        assert list(reference)==expected
        print('Synthetic8192-row GGUF exact arithmetic passed; deliberate0.5 error atrow5 detected. Not model/hardware qualification.')


if __name__=='__main__':main()

#!/usr/bin/env python3
"""Check the actual CLI manifest loader against the diagnostic/serving views."""
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import tempfile


def main():
    base=Path('target/scheduler-fixes');source=(base/'q4-component-local-bundle').resolve()
    helper=runpy.run_path('scripts/profile-scheduler-serving-bundle.py')
    cli=Path('target/scheduler-fix-validation/cli-build/release/skippy').resolve()
    with tempfile.TemporaryDirectory(prefix='q4-manifest-loader-') as directory:
        root=Path(directory)
        def doctor(bundle):
            result=subprocess.run([str(cli),'--output','json','--runtime-cache',str(root/'empty-cache'),
                '--runtime-bundle',str(bundle),'doctor'],capture_output=True,text=True)
            return result
        rejected=doctor(source)
        assert rejected.returncode==1 and 'parse native runtime manifest' in rejected.stderr,rejected
        manifest=json.loads((source/'manifest.json').read_text())
        sha=hashlib.sha256(helper['manifest_bytes'](manifest)).hexdigest()
        view=helper['materialize'](source,root/'serving',sha);result=doctor(view)
        assert result.returncode==0,result.stderr
        accepted=json.loads(result.stdout)
        assert accepted['runtime_issue'] is None and Path(accepted['native_runtime']['path']).resolve()==view.resolve(),accepted
        assert (view/'component-source-manifest.json').read_bytes()==(source/'manifest.json').read_bytes()
        report={'pass':True,'diagnostic_manifest_rejected':True,'serving_manifest_accepted':True,
            'library_files_verified':len(manifest['runtime']['files']),'serving_manifest_sha256':sha,
            'cli_sha256':hashlib.sha256(cli.read_bytes()).hexdigest(),
            'scope':'Local CLI schema/file verification only; no model or remote hardware qualification.'}
        (base/'q4-serving-manifest-selftest.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))


if __name__=='__main__':main()

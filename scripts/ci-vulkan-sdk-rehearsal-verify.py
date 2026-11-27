from pathlib import Path
from lib.ci_artifacts import source, write_json
from lib.ci_vulkan import toolchain
write_json(Path('sdk-rehearsal.json'), {'source':source(),**toolchain('1.4.328.1')})

import sys
from pathlib import Path
import platform

# Add project root to Python path so that 'fea_toolkit' can be imported
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fea_toolkit import __version__, ops_version
from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.io.helper import test_func, mac_file_chooser

print(f"Operating System: {platform.system()}")
print(f"Chipset/Architecture: {platform.machine()}")
print(f'FEA Toolkit Version: {__version__}')
print(f'OpenSees Version: {ops_version()}')

test_func()

chosen_file = mac_file_chooser()
if chosen_file is not None:
    s2k_file = Path(chosen_file)
    print(f'Chosen file: {s2k_file}')
else:
    print('file-chooser failed')

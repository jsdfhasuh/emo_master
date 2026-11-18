"""Stage the current generated Designer sample for Web development."""
from pathlib import Path
from shutil import copyfile


directory = Path(__file__).resolve().parent
source = directory.parents[1] / 'contracts/panel-contract-v1/sample-panels/demo.emoweb'
destination = directory / 'demo.emoweb'
copyfile(source, destination)
print(destination)

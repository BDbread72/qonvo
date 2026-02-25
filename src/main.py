import faulthandler
import sys
import os

log_dir = os.path.join(os.environ.get("APPDATA", ""), "Qonvo", "logs")
os.makedirs(log_dir, exist_ok=True)
_fault_log = open(os.path.join(log_dir, "crash.log"), "a")
faulthandler.enable(file=_fault_log)

from v import ui
from v import app

if __name__ == "__main__":
    mapp = app.App()
    ui.run_app(mapp)

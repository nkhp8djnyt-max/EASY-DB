"""Entry script for PyInstaller (a frozen app needs a plain script, not ``-m easydbms``)."""

from easydbms.app import main

raise SystemExit(main())

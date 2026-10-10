"""Standalone vendor GUI; contains no embedded private signing key."""
from vendor_keygen.ui import main
if __name__=='__main__':
    raise SystemExit(main())

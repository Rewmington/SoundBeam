"""PyInstaller 打包入口：python -m PyInstaller --paths . --onefile --windowed ... desktop/launcher.py"""

from desktop.gui import main

if __name__ == "__main__":
    main()
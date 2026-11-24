@echo off
setlocal
cd /d "%~dp0"

if not exist "assets\icon.ico" (
    echo HATA: assets\icon.ico bulunamadi.
    goto :end
)
if not exist "assets\icon.png" (
    echo HATA: assets\icon.png bulunamadi.
    goto :end
)

echo [1/3] Bagimliliklar kuruluyor (psutil, PySide6, pyinstaller)...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo HATA: pip kurulumu basarisiz.
    goto :end
)

echo.
echo [2/3] PyServiceMonitor.exe derleniyor (onefile, windowed, uac-admin, icon)...
python -m PyInstaller --noconfirm --clean --onefile --windowed --uac-admin ^
    --name PyServiceMonitor ^
    --icon "assets\icon.ico" ^
    --add-data "assets\icon.png;assets" ^
    --exclude-module tkinter ^
    app.py
if errorlevel 1 (
    echo HATA: PyInstaller derlemesi basarisiz.
    goto :end
)

echo.
echo [3/3] Tamam.
echo Cikti: %~dp0dist\PyServiceMonitor.exe
echo Not: Explorer eski ikonu gosterirse dosyayi baska bir klasore kopyalayin
echo       (Windows ikon onbellegi).

:end
echo.
if /i not "%~1"=="nopause" pause
endlocal

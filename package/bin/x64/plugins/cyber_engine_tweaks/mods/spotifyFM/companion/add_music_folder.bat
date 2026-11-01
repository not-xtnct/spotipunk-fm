@echo off
setlocal
cd /d "%~dp0"
echo Add a folder of your own music (mp3, flac, wav, ogg ...) as a radio station.
echo.
set /p "SRC=Folder path (you can drag the folder into this window): "
set "SRC=%SRC:"=%"
set /p "NAME=Station name (e.g. Synthwave FM): "
set /p "FM=FM number, decides its place in the list (e.g. 89.3): "
if "%FM%"=="" (
    call run_companion.bat --add-folder "%SRC%" --name "%NAME%"
) else (
    call run_companion.bat --add-folder "%SRC%" --name "%NAME%" --fm %FM%
)

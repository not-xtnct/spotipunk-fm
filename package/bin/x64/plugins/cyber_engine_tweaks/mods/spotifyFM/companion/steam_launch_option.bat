@echo off
setlocal
cd /d "%~dp0"
title Spotify FM - start with the game
if not exist ".venv\Scripts\pythonw.exe" goto notready
set "LINE="%~dp0.venv\Scripts\pythonw.exe" "%~dp0start_with_game.pyw" %%command%%"
echo(%LINE%| clip
echo.
echo  Spotify FM can now start by itself whenever you play from Steam.
echo.
echo  The line below is already copied. In Steam:
echo    1. Right-click Cyberpunk 2077, then Properties, then General
echo    2. Click into the Launch options box and press Ctrl+V
echo    3. Close the window and play as usual. No need to open the companion.
echo.
echo(  %LINE%
echo.
echo  To turn it off again, clear the Launch options box.
echo  Problems? See launcher.log and companion.log in this folder.
echo.
pause
exit /b 0

:notready
echo The companion isn't set up yet. Double-click run_companion.bat once,
echo log in and pick a playlist, then run this again.
pause
exit /b 1

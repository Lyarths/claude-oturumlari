@echo off
set SCRIPT_DIR=%~dp0
set TARGET=%SCRIPT_DIR%Baslat.bat
set SHORTCUT=%USERPROFILE%\Desktop\Chrome Claude Takipcisi.lnk

powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT%'); $s.TargetPath = '%TARGET%'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.IconLocation = 'C:\Program Files\Google\Chrome\Application\chrome.exe,0'; $s.Description = 'Chrome Profil ve Claude.ai Takipçisi'; $s.Save()"

echo ===================================================
echo Masaustune kisa yol basariyla olusturuldu!
echo Masaustunuzdeki 'Chrome Claude Takipcisi' simgesine cift tiklayarak baslatabilirsiniz.
echo ===================================================
pause

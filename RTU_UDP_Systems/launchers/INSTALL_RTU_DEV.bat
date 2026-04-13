@echo off
chcp 65001 > nul

:: ============================================================
:: Keep CMD window open when run by double-clicking
:: ============================================================
if not defined RTU_KEEP_OPEN (
    cmd /k "set RTU_KEEP_OPEN=1 && %~s0"
    exit /b
)

setlocal EnableDelayedExpansion

:: Ensure working directory is the project root (one level up from launchers/)
cd /d "%~dp0.."

:: Log file
for /f %%I in ('powershell -command "Get-Date -Format yyyyMMdd_HHmmss"') do set "DT=%%I"
set "LOGFILE=%TEMP%\rtu_udp_install_%DT%.txt"

:: Default SSH port
set "SSH_PORT=22"

echo ============================================================
echo   RTU UDP One-Click Installer v1.0.0 (DEV MODE)
echo   Windows PC to CM4-ETH-RS485-BASE-B (Internal Network)
echo ============================================================
echo.
echo   RTU UDP V1.0.0 - Simple UDP Protocol (No TLS/KISA)
echo ============================================================
echo.
echo Log file: %LOGFILE%
echo.

:: ============================================================
:: Get RTU IP
:: ============================================================
echo [Scanning network for Raspberry Pi...]
echo.

for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
    for /f "tokens=1-3 delims=." %%b in ("%%a") do (
        set "NET_PREFIX=%%b.%%c.%%d"
    )
)
set NET_PREFIX=%NET_PREFIX: =%
echo Network: %NET_PREFIX%.x
echo.

echo Scanning... (please wait 10 seconds)
for /L %%i in (1,1,254) do (
    start /b ping -n 1 -w 100 %NET_PREFIX%.%%i > nul 2>&1
)
timeout /t 10 /nobreak > nul

echo.
echo ============================================================
echo   Found devices:
echo ============================================================
set DEVICE_COUNT=0

for /f "tokens=1" %%a in ('arp -a ^| findstr "%NET_PREFIX%"') do (
    echo %%a | findstr "Interface" >nul
    if errorlevel 1 (
        set /a DEVICE_COUNT+=1
        set "IP_!DEVICE_COUNT!=%%a"
        echo   [!DEVICE_COUNT!] %%a
    )
)

echo   [M] Enter IP manually
echo ============================================================
echo.

if !DEVICE_COUNT!==0 (
    set /p "RTU_IP=Enter RTU IP address: "
    goto :CHECK_IP
)

set "CHOICE="
set /p "CHOICE=Select device number or [M]: "

if /i "!CHOICE!"=="M" (
    set /p "RTU_IP=Enter RTU IP address: "
    goto :CHECK_IP
)

if "!CHOICE!"=="" (
    echo ERROR: Selection required
    pause
    goto :EOF
)

call :GET_SELECTED_IP !CHOICE!
if "!RTU_IP!"=="" (
    echo ERROR: Invalid selection [!CHOICE!]
    pause
    goto :EOF
)
goto :CHECK_IP

:GET_SELECTED_IP
set "RTU_IP=!IP_%1!"
goto :EOF

:CHECK_IP

if "%RTU_IP%"=="" (
    echo ERROR: IP address required
    pause
    goto :EOF
)

set "SSH_CMD=ssh -p %SSH_PORT%"
set "SCP_CMD=scp -P %SSH_PORT%"

:: ============================================================
:: Get RTU settings
:: ============================================================
echo.
echo ============================================================
echo   RTU UDP Configuration (DEV MODE)
echo ============================================================
echo   Target: !RTU_IP!
echo ============================================================
echo.

for /f "delims=" %%a in ('powershell -command "$id = Read-Host 'Enter RTU ID [10000001]'; if([string]::IsNullOrEmpty($id)){$id='10000001'}; Write-Output $id"') do set "RTU_ID=%%a"
for /f "delims=" %%a in ('powershell -command "$p = Read-Host 'Enter RTU UDP Port [9100]'; if([string]::IsNullOrEmpty($p)){$p='9100'}; Write-Output $p"') do set "RTU_PORT=%%a"
for /f "delims=" %%a in ('powershell -command "$c = Read-Host 'Enter Communication Period in seconds [60]'; if([string]::IsNullOrEmpty($c)){$c='60'}; Write-Output $c"') do set "RTU_PERIOD=%%a"
for /f "delims=" %%a in ('powershell -command "$h = Read-Host 'Enter Server Host [solarize.ddns.net]'; if([string]::IsNullOrEmpty($h)){$h='solarize.ddns.net'}; Write-Output $h"') do set "SERVER_HOST=%%a"

echo.
echo ============================================================
echo   Installation Settings
echo ============================================================
echo   Target IP  : !RTU_IP!
echo   RTU ID     : !RTU_ID!
echo   UDP Port   : !RTU_PORT!
echo   Comm Period: !RTU_PERIOD! seconds
echo   Server     : !SERVER_HOST!
echo   Protocol   : UDP (No TLS)
echo   SSH        : ENABLED (Dev Mode)
echo ============================================================
echo.

for /f "delims=" %%a in ('powershell -command "$c = Read-Host 'Proceed with installation? [Y/n]'; Write-Output $c"') do set "CONFIRM=%%a"
if /i "!CONFIRM!"=="n" goto :END

if not exist "rtu_program" (
    echo ERROR: rtu_program folder not found
    pause
    goto :EOF
)

:: ============================================================
:: SSH Key Authentication Setup
:: ============================================================
echo.
echo [0/9] Setting up SSH key authentication...
echo.

set "SSH_DIR=%USERPROFILE%\.ssh"
set "SSH_KEY=%SSH_DIR%\id_rsa"

if not exist "%SSH_DIR%" mkdir "%SSH_DIR%"

if not exist "%SSH_KEY%.pub" (
    echo   Generating SSH key pair...
    ssh-keygen -t rsa -b 2048 -f "%SSH_KEY%" -N "" -q
    if errorlevel 1 (
        echo ERROR: Failed to generate SSH key
        pause
        goto :EOF
    )
    echo   SSH key generated.
) else (
    echo   SSH key already exists.
)

echo.
echo   Testing if SSH key is already authorized...
%SSH_CMD% -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=no pi@%RTU_IP% "echo OK" >nul 2>&1
if not errorlevel 1 (
    echo   SSH key already authorized. No password needed.
    goto :START_INSTALL
)

echo.
echo   Copying SSH key to RTU (enter password once)...
echo.
type "%SSH_KEY%.pub" | %SSH_CMD% -o StrictHostKeyChecking=no pi@%RTU_IP% "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys"
if errorlevel 1 (
    echo ERROR: Failed to copy SSH key to RTU
    pause
    goto :EOF
)
echo   SSH key copied successfully.

:START_INSTALL
echo.
echo [1/9] Testing SSH connection...
%SSH_CMD% pi@%RTU_IP% "echo SSH OK"
if errorlevel 1 (
    echo ERROR: SSH connection failed
    pause
    goto :EOF
)

:: Detect CM4 platform
echo.
echo   Detecting platform...
set "PI_MODEL=Unknown"
set "IS_CM4=false"
%SSH_CMD% pi@%RTU_IP% "cat /proc/device-tree/model | tr -d '\0'" > "%TEMP%\pi_model.txt" 2>nul
for /f "usebackq delims=" %%a in ("%TEMP%\pi_model.txt") do set "PI_MODEL=%%a"
del "%TEMP%\pi_model.txt" 2>nul
echo   Platform: !PI_MODEL!
echo !PI_MODEL! | findstr /i /c:"Compute Module" >nul
if not errorlevel 1 set "IS_CM4=true"
if not "!IS_CM4!"=="true" goto :NOT_CM4
echo   -^> CM4 detected: using native UART RS485
goto :CM4_OK
:NOT_CM4
echo.
echo   ERROR: V1.0.0 requires CM4-ETH-RS485-BASE-B.
echo          Detected: !PI_MODEL!
echo.
goto :ERROR_EXIT
:CM4_OK

echo [2/9] Creating directories...
%SSH_CMD% pi@%RTU_IP% "mkdir -p /home/pi/rtu_program/firmware /home/pi/common /home/pi/config /home/pi/backup /home/pi/logs"

echo [3/9] Copying program files...
%SCP_CMD% -r rtu_program/* pi@%RTU_IP%:/home/pi/rtu_program/
%SCP_CMD% -r common/* pi@%RTU_IP%:/home/pi/common/
%SCP_CMD% -r config/* pi@%RTU_IP%:/home/pi/config/

echo [4/9] Configuring RTU settings...
%SSH_CMD% pi@%RTU_IP% "sed -i 's/rtu_id = .*/rtu_id = %RTU_ID%/' /home/pi/config/rtu_config.ini"
%SSH_CMD% pi@%RTU_IP% "sed -i 's/local_port = .*/local_port = %RTU_PORT%/' /home/pi/config/rtu_config.ini"
%SSH_CMD% pi@%RTU_IP% "sed -i 's/communication_period = .*/communication_period = %RTU_PERIOD%/' /home/pi/config/rtu_config.ini"
%SSH_CMD% pi@%RTU_IP% "sed -i 's/primary_host = .*/primary_host = %SERVER_HOST%/' /home/pi/config/rtu_config.ini"

echo [5/9] Installing required packages...
%SSH_CMD% pi@%RTU_IP% "sudo apt-get update -qq && sudo apt-get install -y p7zip-full python3-serial 2>/dev/null"
%SSH_CMD% pi@%RTU_IP% "pip3 install pymodbus --break-system-packages --quiet 2>/dev/null || python3 -m pip install pymodbus --break-system-packages --quiet 2>/dev/null"
echo   -^> p7zip-full, python3-serial(apt), pymodbus

echo [6/9] Configuring RS485 hardware interface...
%SSH_CMD% pi@%RTU_IP% "sudo sed -i '/^dtparam=spi=on/d; /^dtoverlay=spi1-3cs/d' /boot/firmware/config.txt 2>/dev/null"
%SSH_CMD% pi@%RTU_IP% "grep -q '^dtoverlay=uart0' /boot/firmware/config.txt || echo -e '\n# RS485 4CH UART (CM4-ETH-RS485-BASE-B)\ndtoverlay=uart0\ndtoverlay=uart3\ndtoverlay=uart4\ndtoverlay=uart5\ndtoverlay=disable-bt\nenable_uart=1' | sudo tee -a /boot/firmware/config.txt"
%SSH_CMD% pi@%RTU_IP% "sudo systemctl disable serial-getty@ttyAMA0 serial-getty@ttyAMA3 serial-getty@ttyAMA4 serial-getty@ttyAMA5 2>/dev/null"
%SSH_CMD% pi@%RTU_IP% "sudo sed -i 's/console=serial0,[0-9]* //g' /boot/firmware/cmdline.txt 2>/dev/null"
echo   -^> CM4 4CH UART enabled (ttyAMA0/3/4/5)

echo [7/9] Configuring SSH security...
%SSH_CMD% pi@%RTU_IP% "sudo sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config"
%SSH_CMD% pi@%RTU_IP% "sudo sed -i 's/^#*MaxAuthTries.*/MaxAuthTries 5/' /etc/ssh/sshd_config"
%SSH_CMD% pi@%RTU_IP% "grep -q '^PermitRootLogin' /etc/ssh/sshd_config || echo 'PermitRootLogin no' | sudo tee -a /etc/ssh/sshd_config"
%SSH_CMD% pi@%RTU_IP% "sudo systemctl restart sshd 2>/dev/null || sudo systemctl restart ssh 2>/dev/null"
echo   -^> Root login disabled, MaxAuthTries=5

echo [8/9] Installing RTU service...
%SSH_CMD% pi@%RTU_IP% "printf '[Unit]\nDescription=RTU UDP Client Service\nAfter=network.target\n\n[Service]\nType=simple\nUser=pi\nWorkingDirectory=/home/pi/rtu_program\nExecStart=/usr/bin/python3 /home/pi/rtu_program/rtu_client.py\nRestart=always\nRestartSec=5\n\n[Install]\nWantedBy=multi-user.target\n' | sudo tee /etc/systemd/system/rtu.service > /dev/null"
%SSH_CMD% pi@%RTU_IP% "sudo systemctl daemon-reload && sudo systemctl enable rtu && sudo systemctl start rtu"

echo [9/9] Setting file permissions...
%SSH_CMD% pi@%RTU_IP% "chmod 600 /home/pi/config/*.ini 2>/dev/null"
%SSH_CMD% pi@%RTU_IP% "chmod 700 /home/pi/config /home/pi/backup /home/pi/logs 2>/dev/null"
echo   -^> config/*.ini=600, directories=700

echo.
echo ============================================================
echo   Installation Complete! (UDP V1.0.0 DEV MODE)
echo ============================================================
echo.
echo   RTU ID     : !RTU_ID!
echo   IP Address : !RTU_IP!
echo   UDP Port   : !RTU_PORT!
echo   Comm Period: !RTU_PERIOD!s
echo   Server     : !SERVER_HOST!
echo   Protocol   : UDP (No TLS/KISA)
echo   SSH        : ENABLED
echo ============================================================
echo.

for /f "delims=" %%a in ('powershell -command "$r = Read-Host 'Reboot RTU now? [Y/n]'; Write-Output $r"') do set "REBOOT=%%a"
if /i not "!REBOOT!"=="n" (
    echo Rebooting...
    !SSH_CMD! pi@!RTU_IP! "sudo reboot"
)

goto :END

:ERROR_EXIT
echo Installation aborted.
echo.
pause
exit /b 1

:END
echo.
pause

@echo off
rem svngit's `git` shim for Windows (cmd.exe and PowerShell).
rem
rem Put this directory early on PATH and `git` keeps behaving exactly as it
rem always has -- except inside a Subversion working copy, where the command
rem is translated and run against Subversion instead.
rem
rem     cmd:        set "PATH=%PATH%;..."   (put the shim directory FIRST)
rem     PowerShell: $env:PATH = "$(svngit --shim-path);$env:PATH"
rem
rem Dispatch rules, in order:
rem   1. SVNGIT_DISABLE=1               -> always the real git
rem   2. a .git nearer than any .svn    -> the real git
rem   3. a .svn working copy            -> svngit
rem   4. `clone` of an svn:// URL       -> svngit
rem   5. anything else                  -> the real git

setlocal EnableDelayedExpansion

set "SELF_DIR=%~dp0"
if "%SELF_DIR:~-1%"=="\" set "SELF_DIR=%SELF_DIR:~0,-1%"

if not defined SVNGIT_BIN set "SVNGIT_BIN=svngit"

rem Find the first git.exe on PATH that is not this shim's directory.
set "REAL_GIT="
for /f "delims=" %%P in ('where git.exe 2^>nul') do (
    if not defined REAL_GIT (
        set "CANDIDATE=%%~dpP"
        if "!CANDIDATE:~-1!"=="\" set "CANDIDATE=!CANDIDATE:~0,-1!"
        if /i not "!CANDIDATE!"=="%SELF_DIR%" set "REAL_GIT=%%P"
    )
)

if "%SVNGIT_DISABLE%"=="1" goto :real

rem Whichever marker is found first walking up wins, so a git repo checked
rem out inside an svn working copy (or the reverse) still routes correctly.
set "DIR=%CD%"
:walk
if exist "%DIR%\.git" goto :real
if exist "%DIR%\.svn\" goto :svngit
for %%I in ("%DIR%") do set "PARENT=%%~dpI"
if "%PARENT:~-1%"=="\" set "PARENT=%PARENT:~0,-1%"
if /i "%PARENT%"=="%DIR%" goto :outside
set "DIR=%PARENT%"
goto :walk

:outside
rem Outside any working copy, only an unmistakably-Subversion clone URL is
rem worth claiming; an https:// URL could belong to either system.
if /i not "%~1"=="clone" goto :real
for %%A in (%*) do (
    echo %%~A| findstr /b /i /c:"svn://" /c:"svn+ssh://" >nul && goto :svngit
)
goto :real

:svngit
endlocal & "%SVNGIT_BIN%" %*
exit /b %ERRORLEVEL%

:real
if not defined REAL_GIT (
    echo git: no real git found on PATH ^(svngit shim at %SELF_DIR%^) 1>&2
    exit /b 127
)
endlocal & "%REAL_GIT%" %*
exit /b %ERRORLEVEL%

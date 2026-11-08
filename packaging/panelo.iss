; Panelo installer (Inno Setup 6.3 or newer).
; Build:  ISCC /DAppVersion=1.0.0 packaging\panelo.iss
; Input:  dist\Panelo\ (PyInstaller one-folder build)
; Output: dist\Panelo-Setup-<version>.exe
;
; Per-user install, no UAC prompt: %LOCALAPPDATA%\Programs\Panelo.
; User data (%LOCALAPPDATA%\Panelo\Panelo) is never touched by install or uninstall.

#if Ver < EncodeVer(6, 3, 0)
  #error Inno Setup 6.3 or newer is required (x64compatible architecture identifier).
#endif
#ifndef AppVersion
  #error Pass the version: ISCC /DAppVersion=x.y.z panelo.iss
#endif

#define AppName "Panelo"
#define AppExeName "Panelo.exe"
#define AppPublisher "Shaurya"
#define AppURL "https://github.com/Shaurya-289/panelo"
#define AppUserModelID "Panelo.Desktop"
#define RunKey "Software\Microsoft\Windows\CurrentVersion\Run"

[Setup]
; NEVER change AppId: upgrades and the uninstaller find the app by it.
AppId={{8F3C2A71-5D4E-4B9A-9C61-2E7F0B4D9A13}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} Setup
; Non-admin install mode: {autopf} = {userpf} = %LOCALAPPDATA%\Programs,
; {autoprograms}/{autodesktop} = the current user's Start Menu / Desktop.
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DisableDirPage=auto
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=..\dist
OutputBaseFilename=Panelo-Setup-{#AppVersion}
SetupIconFile=..\src\panelo\ui\icons\app-icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
; Fallback only: [Code] force-ends Panelo.exe in PrepareToInstall, which runs
; before Setup's Restart Manager check for files in use.
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Checked by default (no "unchecked" flag).
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Upgrade: drop the previous build's bundle so stale modules/DLLs never linger.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\Panelo\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; AppUserModelID: "{#AppUserModelID}"; Comment: "Notes, tasks, reminders and focus timers"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; AppUserModelID: "{#AppUserModelID}"; Comment: "Notes, tasks, reminders and focus timers"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Program folder only. User data (%LOCALAPPDATA%\Panelo\Panelo) is deliberately kept.
Type: filesandordirs; Name: "{app}\_internal"
Type: dirifempty; Name: "{app}"

[Code]
procedure StopPanelo();
var
  ResultCode: Integer;
begin
  { Panelo hides to the tray on WM_CLOSE, so a normal close does not quit it:
    end it outright (SQLite WAL survives). taskkill exits 128 if none is running. }
  if Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM {#AppExeName}', '', SW_HIDE,
          ewWaitUntilTerminated, ResultCode) and (ResultCode = 0) then
    Sleep(800);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  { Runs after the Ready page (user has confirmed) and before the files-in-use check. }
  StopPanelo();
  Result := '';
end;

procedure RemoveOwnStartupEntry();
var
  Value: String;
begin
  { Only remove "Start with Windows" if it points at this installation's exe. }
  if RegQueryStringValue(HKCU, '{#RunKey}', '{#AppName}', Value) then
    if Pos(Lowercase(ExpandConstant('{app}\{#AppExeName}')), Lowercase(Value)) > 0 then
      RegDeleteValue(HKCU, '{#RunKey}', '{#AppName}');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  { usUninstall: after the user confirmed, before any file is removed. }
  if CurUninstallStep = usUninstall then
  begin
    StopPanelo();
    RemoveOwnStartupEntry();
  end;
end;

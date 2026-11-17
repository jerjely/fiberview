; Inno Setup script for FiberView - per-user install, no admin rights.
; Built by the release workflow:  ISCC /DMyAppVersion=1.2.3 /DMyAppNumeric=1.2.3 install\windows_installer.iss
; Input: dist\FiberView\ (PyInstaller)   Output: dist\FiberView-<version>-Windows-setup.exe

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
; MyAppNumeric: numbers only (file properties); MyAppVersion may be e.g. "0.0.0-test.abc1234"
#ifndef MyAppNumeric
  #define MyAppNumeric "0.0.0"
#endif
#define MyAppName "FiberView"
#define MyAppExe "FiberView.exe"

[Setup]
AppId={{8C1F4C2E-5B7A-4D6B-9A0E-FB1E2C3D4A55}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Gergely Szarka, University of Pecs
AppPublisherURL=https://github.com/jerjely/FiberView
AppSupportURL=https://github.com/jerjely/FiberView/issues
AppUpdatesURL=https://github.com/jerjely/FiberView/releases
VersionInfoVersion={#MyAppNumeric}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=FiberView-{#MyAppVersion}-Windows-setup
SetupIconFile=..\assets\fiberview.ico
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; remove the previous version's libraries before installing the new ones (recordings are never here)
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\FiberView\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; AppUserModelID: "PTE.FiberView"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; AppUserModelID: "PTE.FiberView"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

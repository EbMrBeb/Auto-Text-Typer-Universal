; Auto Text Typer -- Inno Setup installer script.
;
; Consumes the onedir build PyInstaller produces at dist/AutoTextTyper/
; (see packaging/pyinstaller/auto_text_typer.spec and build_windows.ps1,
; which runs both in sequence). Requires at least the app's version:
;
;   iscc /DAppVersion=1.2.3 packaging\windows\installer.iss
;
; AppDir / OutputDir / OutputBaseFilename all default to sensible values
; relative to this file's own location if not overridden with another
; /D define, so the one-define invocation above is enough on its own.
#ifndef AppVersion
  #error "AppVersion must be defined, e.g. iscc /DAppVersion=1.2.3 installer.iss"
#endif

#define AppName "Auto Text Typer"
#define AppExeName "AutoTextTyper.exe"
#define AppPublisher "Braxton Gorman"
#define AppURL "https://github.com/EbMrBeb/Auto-Text-Typer-Universal"

; SourcePath is this .iss file's own directory (packaging\windows\), not
; necessarily backslash-terminated -- AddBackslash() before appending is
; the documented-safe way to build further paths from it. Two levels up
; from there is the repo root.
#define RepoRoot AddBackslash(SourcePath) + "..\..\"

#ifndef AppDir
  #define AppDir RepoRoot + "dist\AutoTextTyper"
#endif
#ifndef OutputDir
  #define OutputDir RepoRoot + "dist"
#endif
#ifndef OutputBaseFilename
  #define OutputBaseFilename "AutoTextTyper-" + AppVersion + "-Windows-x64-Setup"
#endif

[Setup]
; Fixed per app (regenerate only if this ever forks into a distinct
; product) -- Inno Setup uses it to recognise upgrades vs. new installs.
AppId={{F1FC94C2-8B0C-4D92-98D1-FC6FF1BCC63A}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Installs per-user with no admin/UAC prompt by default, but still lets
; the user elevate for an all-users install from the wizard's first page.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
SetupIconFile={#RepoRoot}assets\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; A license page only makes sense once the project actually has a LICENSE
; file; skip it entirely rather than point at something that may not exist.
#ifexist RepoRoot + "LICENSE"
LicenseFile={#RepoRoot}LICENSE
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#AppDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

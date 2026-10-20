
#include "components.iss"

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppName=Backtalk
AppVersion={#AppVersion}
AppPublisher=7th Life Studios
AppPublisherURL=https://github.com/7th-Life-Studios/Backtalk
AppSupportURL=https://github.com/7th-Life-Studios/Backtalk
AppUpdatesURL=https://github.com/7th-Life-Studios/Backtalk
DefaultDirName={autopf}\Backtalk
DefaultGroupName=Backtalk
UninstallDisplayName=Backtalk
AppMutex=Backtalk.SingleInstance.v1
DisableProgramGroupPage=yes
DisableWelcomePage=no
AlwaysShowComponentsList=yes
DirExistsWarning=no
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=..\assets\backtalk.ico
UninstallDisplayIcon={app}\Backtalk\assets\backtalk.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

#ifdef OFFLINE
OutputBaseFilename=Backtalk-Setup-Offline
#else
OutputBaseFilename=Backtalk-Setup
#endif
OutputDir=..\tools\dist\installer

VersionInfoVersion=1.0.0.0
VersionInfoProductVersion=1.0.0.0
VersionInfoDescription=Backtalk Setup
VersionInfoProductName=Backtalk
VersionInfoCompany=7th Life Studios
VersionInfoCopyright=Copyright (c) 2026 7th Life Studios

[Types]
Name: "full"; Description: "Full installation (all languages)"
Name: "compact"; Description: "Compact installation (English only)"
Name: "custom"; Description: "Custom installation"; Flags: iscustom

[Components]
Name: "app"; Description: "Backtalk Application"; Types: full compact custom; Flags: fixed; ExtraDiskSpaceRequired: {#app_InstalledSize}
Name: "engine"; Description: "Kokoro Voice Engine"; Types: full compact custom; Flags: fixed; ExtraDiskSpaceRequired: {#engine_InstalledSize}
Name: "translation"; Description: "Translation Packs"; Types: full custom
Name: "translation\chinese"; Description: "Chinese"; Types: full; Flags: dontinheritcheck; ExtraDiskSpaceRequired: {#translation_chinese_InstalledSize}
Name: "translation\french"; Description: "French"; Types: full; Flags: dontinheritcheck; ExtraDiskSpaceRequired: {#translation_french_InstalledSize}
Name: "translation\hindi"; Description: "Hindi"; Types: full; Flags: dontinheritcheck; ExtraDiskSpaceRequired: {#translation_hindi_InstalledSize}
Name: "translation\italian"; Description: "Italian"; Types: full; Flags: dontinheritcheck; ExtraDiskSpaceRequired: {#translation_italian_InstalledSize}
Name: "translation\japanese"; Description: "Japanese"; Types: full; Flags: dontinheritcheck; ExtraDiskSpaceRequired: {#translation_japanese_InstalledSize}
Name: "translation\portuguese"; Description: "Portuguese"; Types: full; Flags: dontinheritcheck; ExtraDiskSpaceRequired: {#translation_portuguese_InstalledSize}
Name: "translation\spanish"; Description: "Spanish"; Types: full; Flags: dontinheritcheck; ExtraDiskSpaceRequired: {#translation_spanish_InstalledSize}

[Files]
Source: "..\assets\backtalk.ico"; DestDir: "{app}\Backtalk\assets"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD-PARTY-LICENSES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs
#ifdef OFFLINE
Source: "..\tools\dist\Backtalk\*"; DestDir: "{app}\Backtalk"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: app
Source: "..\tools\dist\engine\*"; DestDir: "{app}\engine"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: engine
Source: "..\tools\dist\packs\chinese\*"; DestDir: "{app}\packs"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: translation\chinese
Source: "..\tools\dist\packs\french\*"; DestDir: "{app}\packs"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: translation\french
Source: "..\tools\dist\packs\hindi\*"; DestDir: "{app}\packs"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: translation\hindi
Source: "..\tools\dist\packs\italian\*"; DestDir: "{app}\packs"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: translation\italian
Source: "..\tools\dist\packs\japanese\*"; DestDir: "{app}\packs"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: translation\japanese
Source: "..\tools\dist\packs\portuguese\*"; DestDir: "{app}\packs"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: translation\portuguese
Source: "..\tools\dist\packs\spanish\*"; DestDir: "{app}\packs"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: translation\spanish
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Icons]
Name: "{group}\Backtalk"; Filename: "{app}\Backtalk\Backtalk.exe"; IconFilename: "{app}\Backtalk\assets\backtalk.ico"
Name: "{group}\{cm:UninstallProgram,Backtalk}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Backtalk"; Filename: "{app}\Backtalk\Backtalk.exe"; IconFilename: "{app}\Backtalk\assets\backtalk.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\Backtalk\Backtalk.exe"; Description: "{cm:LaunchProgram,Backtalk}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\Backtalk"
Type: filesandordirs; Name: "{app}\engine"
Type: filesandordirs; Name: "{app}\packs"

[Code]
#ifndef OFFLINE
var
  DownloadPage: TDownloadWizardPage;
  DownloadSizeLabel: TLabel;

function FormatByteSize(Bytes: Int64): String;
begin
  if Bytes >= 1073741824 then
    Result := Format('%.2f GB', [Extended(Bytes) / 1073741824.0])
  else if Bytes >= 1048576 then
    Result := Format('%.1f MB', [Extended(Bytes) / 1048576.0])
  else if Bytes >= 1024 then
    Result := Format('%.1f KB', [Extended(Bytes) / 1024.0])
  else
    Result := Format('%d bytes', [Bytes]);
end;

procedure UpdateDownloadSize(Sender: TObject);
var
  TotalDownload: Int64;
  TotalInstalled: Int64;
begin
  TotalDownload := 0;
  TotalInstalled := 0;
  if WizardIsComponentSelected('app') then
  begin
    TotalDownload := TotalDownload + {#app_Size};
    TotalInstalled := TotalInstalled + {#app_InstalledSize};
  end;
  if WizardIsComponentSelected('engine') then
  begin
    TotalDownload := TotalDownload + {#engine_Size};
    TotalInstalled := TotalInstalled + {#engine_InstalledSize};
  end;
  if WizardIsComponentSelected('translation\chinese') then
  begin
    TotalDownload := TotalDownload + {#translation_chinese_Size};
    TotalInstalled := TotalInstalled + {#translation_chinese_InstalledSize};
  end;
  if WizardIsComponentSelected('translation\french') then
  begin
    TotalDownload := TotalDownload + {#translation_french_Size};
    TotalInstalled := TotalInstalled + {#translation_french_InstalledSize};
  end;
  if WizardIsComponentSelected('translation\hindi') then
  begin
    TotalDownload := TotalDownload + {#translation_hindi_Size};
    TotalInstalled := TotalInstalled + {#translation_hindi_InstalledSize};
  end;
  if WizardIsComponentSelected('translation\italian') then
  begin
    TotalDownload := TotalDownload + {#translation_italian_Size};
    TotalInstalled := TotalInstalled + {#translation_italian_InstalledSize};
  end;
  if WizardIsComponentSelected('translation\japanese') then
  begin
    TotalDownload := TotalDownload + {#translation_japanese_Size};
    TotalInstalled := TotalInstalled + {#translation_japanese_InstalledSize};
  end;
  if WizardIsComponentSelected('translation\portuguese') then
  begin
    TotalDownload := TotalDownload + {#translation_portuguese_Size};
    TotalInstalled := TotalInstalled + {#translation_portuguese_InstalledSize};
  end;
  if WizardIsComponentSelected('translation\spanish') then
  begin
    TotalDownload := TotalDownload + {#translation_spanish_Size};
    TotalInstalled := TotalInstalled + {#translation_spanish_InstalledSize};
  end;

  if Assigned(DownloadSizeLabel) then
    DownloadSizeLabel.Caption := 'Download size: ' + FormatByteSize(TotalDownload);

  WizardForm.ComponentsDiskSpaceLabel.Caption :=
    'Current selection requires at least ' + FormatByteSize(TotalInstalled) + ' of disk space.';
end;

function OnDownloadProgress(const Url, FileName: String; const Progress, ProgressMax: Int64): Boolean;
begin
  Result := True;
end;
#endif

procedure InitializeWizard();
begin
#ifndef OFFLINE
  DownloadPage := CreateDownloadPage(SetupMessage(msgWizardPreparing), SetupMessage(msgPreparingDesc), @OnDownloadProgress);
  
  WizardForm.ComponentsList.Height := WizardForm.ComponentsList.Height - 22;
  WizardForm.ComponentsDiskSpaceLabel.Top := WizardForm.ComponentsList.Top + WizardForm.ComponentsList.Height + 4;

  DownloadSizeLabel := TLabel.Create(WizardForm);
  DownloadSizeLabel.Parent := WizardForm.SelectComponentsPage;
  DownloadSizeLabel.Left := WizardForm.ComponentsDiskSpaceLabel.Left;
  DownloadSizeLabel.Top := WizardForm.ComponentsDiskSpaceLabel.Top + WizardForm.ComponentsDiskSpaceLabel.Height + 4;
  DownloadSizeLabel.Caption := 'Download size: Calculating...';
  WizardForm.ComponentsList.OnClickCheck := @UpdateDownloadSize;
#endif
end;

procedure CurPageChanged(CurPageID: Integer);
begin
#ifndef OFFLINE
  if CurPageID = wpSelectComponents then
    UpdateDownloadSize(nil);
#endif
end;

#ifndef OFFLINE
function NextButtonClick(CurPageID: Integer): Boolean;
var
  RetryDownload: Boolean;
  ErrText: String;
begin
  Result := True;
  if CurPageID = wpReady then
  begin
    DownloadPage.Clear;

    if WizardIsComponentSelected('app') then
      DownloadPage.Add('{#app_Url}', '{#app_Archive}', '{#app_SHA256}');

    if WizardIsComponentSelected('engine') then
      DownloadPage.Add('{#engine_Url}', '{#engine_Archive}', '{#engine_SHA256}');

    if WizardIsComponentSelected('translation\chinese') then
      DownloadPage.Add('{#translation_chinese_Url}', '{#translation_chinese_Archive}', '{#translation_chinese_SHA256}');

    if WizardIsComponentSelected('translation\french') then
      DownloadPage.Add('{#translation_french_Url}', '{#translation_french_Archive}', '{#translation_french_SHA256}');

    if WizardIsComponentSelected('translation\hindi') then
      DownloadPage.Add('{#translation_hindi_Url}', '{#translation_hindi_Archive}', '{#translation_hindi_SHA256}');

    if WizardIsComponentSelected('translation\italian') then
      DownloadPage.Add('{#translation_italian_Url}', '{#translation_italian_Archive}', '{#translation_italian_SHA256}');

    if WizardIsComponentSelected('translation\japanese') then
      DownloadPage.Add('{#translation_japanese_Url}', '{#translation_japanese_Archive}', '{#translation_japanese_SHA256}');

    if WizardIsComponentSelected('translation\portuguese') then
      DownloadPage.Add('{#translation_portuguese_Url}', '{#translation_portuguese_Archive}', '{#translation_portuguese_SHA256}');

    if WizardIsComponentSelected('translation\spanish') then
      DownloadPage.Add('{#translation_spanish_Url}', '{#translation_spanish_Archive}', '{#translation_spanish_SHA256}');

    repeat
      RetryDownload := False;
      DownloadPage.Show;
      try
        try
          DownloadPage.Download;
          Result := True;
        except
          if DownloadPage.AbortedByUser then
          begin
            Log('Download aborted by user.');
            Result := False;
            Exit;
          end
          else
          begin
            ErrText := GetExceptionMessage;
            Log('Download error: ' + ErrText);
            if (Pos('hash', Lowercase(ErrText)) > 0) or
               (Pos('mismatch', Lowercase(ErrText)) > 0) or
               (Pos('checksum', Lowercase(ErrText)) > 0) then
            begin
              if SuppressibleMsgBox('The downloaded file arrived damaged or incomplete.' + #13#10#13#10 +
                                    'Would you like to download it again?',
                                    mbError, MB_YESNO, IDNO) = IDYES then
                RetryDownload := True
              else
              begin
                Result := False;
                Exit;
              end;
            end
            else if Pos('404', ErrText) > 0 then
            begin
              if SuppressibleMsgBox('Download error: ' + ErrText + #13#10#13#10 +
                                    'This file is not available on the download server yet. Please try again later, or use the offline installer.' + #13#10#13#10 +
                                    'Would you like to retry?',
                                    mbError, MB_YESNO, IDNO) = IDYES then
                RetryDownload := True
              else
              begin
                Result := False;
                Exit;
              end;
            end
            else
            begin
              if SuppressibleMsgBox('Download error: ' + ErrText + #13#10#13#10 +
                                    'Please check your network connection.' + #13#10#13#10 +
                                    'Would you like to retry?',
                                    mbError, MB_YESNO, IDNO) = IDYES then
                RetryDownload := True
              else
              begin
                Result := False;
                Exit;
              end;
            end;
          end;
        end;
      finally
        DownloadPage.Hide;
      end;
    until not RetryDownload;
  end;
end;

function ExtractArchive(ArchiveFile: String): Boolean;
var
  TarPath: String;
  ArchivePath: String;
  AppDir: String;
  ResultCode: Integer;
begin
  Result := True;
  TarPath := ExpandConstant('{sys}\tar.exe');
  ArchivePath := ExpandConstant('{tmp}\' + ArchiveFile);
  AppDir := ExpandConstant('{app}');

  if not FileExists(ArchivePath) then
    Exit;

  if not FileExists(TarPath) then
  begin
    Log('Extraction failed for ' + ArchiveFile + ': tar.exe not found.');
    Result := False;
    Exit;
  end;

  WizardForm.StatusLabel.Caption := 'Extracting ' + ArchiveFile + '...';

  if not Exec(TarPath, '-xf "' + ArchivePath + '" -C "' + AppDir + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
  begin
    Log('Extraction failed for ' + ArchiveFile + ' (exit code ' + IntToStr(ResultCode) + ').');
    Result := False;
    Exit;
  end;

  DeleteFile(ArchivePath);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  RequiredOk: Boolean;
  FailedPacks: String;
begin
  if CurStep = ssInstall then
  begin
    ForceDirectories(ExpandConstant('{app}'));

    RequiredOk := ExtractArchive('{#app_Archive}');
    if RequiredOk then
      RequiredOk := ExtractArchive('{#engine_Archive}');

    if not RequiredOk then
    begin
      DelTree(ExpandConstant('{app}\Backtalk'), True, True, True);
      DelTree(ExpandConstant('{app}\engine'), True, True, True);
      DelTree(ExpandConstant('{app}\packs'), True, True, True);
      SuppressibleMsgBox('The installation could not be completed. Nothing was left behind on your computer.',
                          mbCriticalError, MB_OK, IDOK);
      Abort;
    end;

    FailedPacks := '';

    if not ExtractArchive('{#translation_chinese_Archive}') then
      FailedPacks := FailedPacks + 'Chinese' + #13#10;
    if not ExtractArchive('{#translation_french_Archive}') then
      FailedPacks := FailedPacks + 'French' + #13#10;
    if not ExtractArchive('{#translation_hindi_Archive}') then
      FailedPacks := FailedPacks + 'Hindi' + #13#10;
    if not ExtractArchive('{#translation_italian_Archive}') then
      FailedPacks := FailedPacks + 'Italian' + #13#10;
    if not ExtractArchive('{#translation_japanese_Archive}') then
      FailedPacks := FailedPacks + 'Japanese' + #13#10;
    if not ExtractArchive('{#translation_portuguese_Archive}') then
      FailedPacks := FailedPacks + 'Portuguese' + #13#10;
    if not ExtractArchive('{#translation_spanish_Archive}') then
      FailedPacks := FailedPacks + 'Spanish' + #13#10;

    if FailedPacks <> '' then
    begin
      SuppressibleMsgBox('The following language pack(s) could not be installed:' + #13#10#13#10 +
                          FailedPacks + #13#10 +
                          'The rest of Backtalk installed fine. You can run setup again later to add ' +
                          'that language.',
                          mbInformation, MB_OK, IDOK);
    end;
  end;
end;
#endif

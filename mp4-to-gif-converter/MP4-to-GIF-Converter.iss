; Inno Setup Script for MP4-to-GIF-Converter
; この行はコメントです。セミコロンで始まります。

[Setup]
; アプリの基本情報
AppName=MP4-to-GIF-Converter
AppVersion=1.2
AppPublisher=k3
AppPublisherURL=https://www.vector.co.jp/soft/winnt/art/se527852.html?ds

; 管理者権限を要求せず、ユーザーごとにインストールする
PrivilegesRequired=lowest

; 64ビットOSでは64ビットモードでインストールする
ArchitecturesInstallIn64BitMode=x64compatible
DefaultDirName={userpf}\MP4-to-GIF-Converter
DefaultGroupName=MP4-to-GIF-Converter
DisableProgramGroupPage=yes
; 出力されるインストーラーのファイル名
OutputBaseFilename=MP4-to-GIF-Converter_setup
; 圧縮設定（必須）
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; アンインストーラーにアプリアイコンを表示
UninstallDisplayIcon={app}\mp4-to-gif-converter.exe

[Languages]
Name: "japanese"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; アプリの配布フォルダの中身をすべてコピーします。
Source: "C:\Users\kakik\Desktop\mp4-to-gif-converter-v1.2\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs


[Icons]
; スタートメニューとデスクトップにショートカットを作成します。
Name: "{group}\MP4-to-GIF-Converter"; Filename: "{app}\MP4-to-GIF-Converter.exe"
Name: "{autodesktop}\MP4-to-GIF-Converter"; Filename: "{app}\MP4-to-GIF-Converter.exe"; Tasks: desktopicon

[Run]
; インストール完了後にアプリを起動するオプション（任意）
Filename: "{app}\MP4-to-GIF-Converter.exe"; Description: "{cm:LaunchProgram,MP4-to-GIF-Converter}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; アンインストール時にAppDataに作成したフォルダを削除する
Type: filesandordirs; Name: "{localappdata}\MP4-to-GIF-Converter"

{ lib
, callPackage
, clangStdenv
, fetchFromGitHub
, fetchpatch
, cmake
, pkg-config
, wrapQtAppsHook
, openssl
, zlib
, libpng
, libglvnd
, xorg
, libevdev
, curl
, qtwebengine
, pulseaudio
, qt6
, glfw
, withQtWebview ? true
, withQtErrorWindow ? true
}:

let
  common = callPackage ./common.nix { };
in
# gcc doesn't support __has_feature
clangStdenv.mkDerivation rec {
  pname = "mcpelauncher-client";
  version = common.version;

  src = fetchFromGitHub {
    owner = common.owner;
    repo = "mcpelauncher-manifest";
    rev = "v${version}";
    fetchSubmodules = true;
    hash = "sha256-PmCq6Zgtp17UV0kIbNouFwj/DMiTqwE31+tTb2LUp5o=";
  };

  patches = [
    ./dont_download_nlohmann_json.patch
    ./dont_download_glfw_client.patch
    # These are upcoming changes that have been merged upstream. Once these get in a release, remove these patches.
    (fetchpatch {
        url = "https://github.com/minecraft-linux/game-window/commit/feea8c0e0720eea7093ed95745c17f36d6c40671.diff";
        sha256 = "sha256-u4uveoKwwklEooT+i+M9kZ0PshjL1IfWhlltmulsQJo=";
        stripLen = 1;
        extraPrefix = "game-window/";
    })
    (fetchpatch {
        url = "https://github.com/minecraft-linux/mcpelauncher-client/commit/db9c31e46d7367867c85a0d0aba42c8144cdf795.diff";
        sha256 = "sha256-za/9oZYwKCYyZ1BXQ/zeEjRy81B1NpTlPHEfWAOtzHk=";
        stripLen = 1;
        extraPrefix = "mcpelauncher-client/";
    })
  ];
  
  # FORTIFY_SOURCE breaks libc_shim and the project will fail to compile
  hardeningDisable = [ "fortify" ];

  nativeBuildInputs = [
    cmake
    pkg-config
  ] ++ lib.optionals (withQtWebview || withQtErrorWindow) [
    wrapQtAppsHook
  ];

  buildInputs = [
    openssl
    common.nlohmann_json_373
    zlib
    libpng
    libglvnd
    xorg.libX11
    xorg.libXi
    xorg.libXtst
    libevdev
    curl
    pulseaudio
    qt6.qttools
    glfw
  ] ++ lib.optionals withQtWebview [
    qt6.qtwebengine
  ];

  cmakeFlags = [
    "-DUSE_OWN_CURL=OFF"
    "-DENABLE_DEV_PATHS=OFF"
    "-Wno-dev"
    "-DCMAKE_BUILD_TYPE=Release"
    "-DGAMEWINDOW_SYSTEM=GLFW"
    "-DUSE_SDL3_AUDIO=OFF"
  ] ++ lib.optionals (!withQtWebview) [
    "-DBUILD_WEBVIEW=OFF"
    "-DXAL_WEBVIEW_USE_CLI=ON"
    "-DXAL_WEBVIEW_USE_QT=OFF"
  ] ++ lib.optionals (!withQtErrorWindow) [
    "-DENABLE_QT_ERROR_UI=OFF"
  ];

  meta = with lib; {
    inherit (common) homepage maintainers platforms badPlatforms;
    description = "${common.description} - CLI launcher";
    license = licenses.gpl3Plus;
  };
}

{ lib
, callPackage
, stdenv
, fetchFromGitHub
, cmake
, pkg-config
, wrapQtAppsHook
, zlib
, libzip
, curl
, protobuf
, qt6
, glfw
, withPlayLicenseCheck ? true
}:
# The package does not accept style overrides at this moment. If you configured your Nix to override styles of Qt applications, this app won't launch.
# Pass this variable to remove your override QT_STYLE_OVERRIDE=""
# https://github.com/minecraft-linux/mcpelauncher-ui-qt/issues/25

let
  common = callPackage ./common.nix { };
in
stdenv.mkDerivation rec {
  pname = "mcpelauncher-ui-qt";
  version = common.version;

  src = fetchFromGitHub {
    owner = common.owner;
    repo = "mcpelauncher-ui-manifest";
    rev = "v${version}";
    fetchSubmodules = true;
    hash = "sha256-R9wE1lS7x1IIPgVahXjF5Yg2ca+GsiQuF41pWf2edXY=";
  };

  patches = [
    ./dont_download_glfw_ui.patch
  ];

  nativeBuildInputs = [
    cmake
    pkg-config
    wrapQtAppsHook
  ];

  buildInputs = [
    zlib
    libzip
    curl
    protobuf
    qt6.qtwebengine
    qt6.qtsvg
    glfw
  ];

  cmakeFlags = [
    "-Wno-dev"
    "-DCMAKE_BUILD_TYPE=Release"
  ] ++ lib.optionals (!withPlayLicenseCheck) [
    "-DLAUNCHER_ENABLE_GOOGLE_PLAY_LICENCE_CHECK=OFF"
  ];

  meta = with lib; {
    inherit (common) homepage maintainers platforms badPlatforms;
    description = "${common.description} - Qt6 UI";
    license = licenses.gpl3Plus;
  };
}

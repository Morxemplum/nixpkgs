{ lib
, callPackage
}:

{
  owner = "minecraft-linux";

  version = "1.1.2-qt6";

  nlohmann_json_373 = callPackage ./nlohmann_json_373_dep.nix { };

  description = "Unofficial *NIX launcher of Minecraft: Bedrock Edition using Android";

  homepage = "https://minecraft-linux.github.io/";

  maintainers = with lib.maintainers; [ aleksana morxemplum ];

  platforms = lib.platforms.unix;
  
  badPlatforms = lib.platforms.darwin;
}

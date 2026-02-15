{
  description = "meshcore-uconsole GTK development shell";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        lib = pkgs.lib;

        python = pkgs.python312.withPackages (ps: with ps; [
          pip
          pygobject3
          pycairo
          segno
        ]);

        runtimeLibs = [
          pkgs.gtk4
          pkgs.libadwaita
          pkgs.libshumate
          pkgs.libsoup_3
          pkgs.glib-networking
          pkgs.pango
          pkgs.gdk-pixbuf
          pkgs.glib
          pkgs.cairo
          pkgs.graphene
          pkgs.harfbuzz
        ];

        schemaPkgs = [
          pkgs.gsettings-desktop-schemas
          pkgs.gtk4
          pkgs.libadwaita
          pkgs.glib
        ];

        typelibPkgs = [
          pkgs.gtk4
          pkgs.libadwaita
          pkgs.libshumate
          pkgs.gdk-pixbuf
          pkgs.pango
          pkgs.glib
          pkgs.graphene
          pkgs.harfbuzz
        ];
        # Nix puts GSettings schemas under share/gsettings-schemas/<name>/, not share/.
        schemaDataDirs = map (p: "${p}/share/gsettings-schemas/${p.name}") schemaPkgs;
        schemaDirs = map (p: "${p}/share/gsettings-schemas/${p.name}/glib-2.0/schemas") schemaPkgs;

        # Generate a fontconfig config pointing to Nix-provided fonts so Pango
        # can render text in headless / nix develop --command environments.
        fontsConf = pkgs.makeFontsConf {
          fontDirectories = [ pkgs.dejavu_fonts pkgs.noto-fonts-color-emoji ];
        };
      in
      {
        devShells.default = pkgs.mkShell {
          packages = [
            python
            pkgs.uv
            pkgs.gobject-introspection
            pkgs.pkg-config
          ] ++ runtimeLibs;

          # Help GI and dynamic linker find typelibs and native libs in dev shell.
          # These attributes are available to `nix develop --command` (unlike shellHook exports).
          GI_TYPELIB_PATH = lib.makeSearchPath "lib/girepository-1.0" typelibPkgs;
          LD_LIBRARY_PATH = lib.makeLibraryPath runtimeLibs;
          DYLD_FALLBACK_LIBRARY_PATH = lib.makeLibraryPath runtimeLibs;
          PKG_CONFIG_PATH = lib.makeSearchPath "lib/pkgconfig" runtimeLibs;
          GSETTINGS_SCHEMA_DIR = lib.concatStringsSep ":" schemaDirs;
          GIO_EXTRA_MODULES = "${pkgs.glib-networking}/lib/gio/modules";
          FONTCONFIG_FILE = fontsConf;

          shellHook = ''
            export UV_PROJECT_ENVIRONMENT="$PWD/.venv"
            export XDG_DATA_DIRS="${lib.concatStringsSep ":" schemaDataDirs}:''${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
            echo "Entered meshcore-uconsole dev shell"
            echo "Use: uv venv && uv sync"
          '';
        };
      });
}

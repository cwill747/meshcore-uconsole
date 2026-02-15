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
          GSETTINGS_SCHEMA_DIR = lib.makeSearchPath "share/glib-2.0/schemas" schemaPkgs;
          GIO_EXTRA_MODULES = "${pkgs.glib-networking}/lib/gio/modules";

          shellHook = ''
            export UV_PROJECT_ENVIRONMENT="$PWD/.venv"
            export XDG_DATA_DIRS="${lib.makeSearchPath "share" schemaPkgs}:''${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
            export GSETTINGS_SCHEMA_DIR="${lib.makeSearchPath "share/glib-2.0/schemas" schemaPkgs}"
            export GIO_EXTRA_MODULES="${pkgs.glib-networking}/lib/gio/modules"
            echo "Entered meshcore-uconsole dev shell"
            echo "Use: uv venv && uv sync"
          '';
        };
      });
}

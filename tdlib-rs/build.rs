// Copyright 2020 - developers of the `grammers` project.
// Copyright 2021 - developers of the `tdlib-rs` project.
// Copyright 2024 - developers of the `tgt` and `tdlib-rs` projects.
//
// Licensed under the Apache License, Version 2.0 <LICENSE-APACHE or
// https://www.apache.org/licenses/LICENSE-2.0> or the MIT license
// <LICENSE-MIT or https://opensource.org/licenses/MIT>, at your
// option. This file may not be copied, modified, or distributed
// except according to those terms.
#[cfg(all(
    feature = "static",
    any(feature = "local-tdlib", feature = "download-tdlib")
))]
#[path = "native_libraries.rs"]
mod native_libraries;

use std::env;
use std::fs::File;
use std::io::{BufWriter, Read, Write};
use std::path::Path;
use tdlib_rs_gen::generate_rust_code;
use tdlib_rs_parser::parse_tl_file;
use tdlib_rs_parser::tl::Definition;

/// Load the type language definitions from a certain file.
/// Parse errors will be printed to `stderr`, and only the
/// valid results will be returned.
fn load_tl(file: &str) -> std::io::Result<Vec<Definition>> {
    let mut file = File::open(file)?;
    let mut contents = String::new();
    file.read_to_string(&mut contents)?;
    Ok(parse_tl_file(contents)
        .filter_map(|d| match d {
            Ok(d) => Some(d),
            Err(e) => {
                eprintln!("TL: parse error: {e:?}");
                None
            }
        })
        .collect())
}

#[cfg(feature = "local-tdlib")]
/// Copy all files from a directory to another.
fn copy_dir_all(src: impl AsRef<Path>, dst: impl AsRef<Path>) -> std::io::Result<()> {
    std::fs::create_dir_all(&dst)?;
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        let ty = entry.file_type()?;
        if ty.is_dir() {
            copy_dir_all(entry.path(), dst.as_ref().join(entry.file_name()))?;
        } else {
            std::fs::copy(entry.path(), dst.as_ref().join(entry.file_name()))?;
        }
    }
    Ok(())
}

#[cfg(feature = "local-tdlib")]
/// Copy all the tdlib folder find in the LOCAL_TDLIB_PATH environment variable to the OUT_DIR/tdlib folder
fn copy_local_tdlib() {
    match env::var("LOCAL_TDLIB_PATH") {
        Ok(tdlib_path) => {
            let out_dir = env::var("OUT_DIR").unwrap();
            let prefix = format!("{out_dir}/tdlib");
            copy_dir_all(Path::new(&tdlib_path), Path::new(&prefix)).unwrap();
        }
        Err(_) => {
            panic!("The LOCAL_TDLIB_PATH env variable must be set to the path of the tdlib folder");
        }
    };
}

#[cfg(any(feature = "download-tdlib", feature = "local-tdlib"))]
/// Build the project using the generic build configuration.
/// The current supported platforms are:
/// - Android x86_64
/// - Android aarch64
/// - Linux x86_64
/// - Linux aarch64
/// - Windows x86_64
/// - Windows aarch64
/// - MacOS x86_64
/// - MacOS aarch64
fn generic_build(_tdlib_version: &str) {
    let out_dir = env::var("OUT_DIR").unwrap();
    let target_os = env::var("CARGO_CFG_TARGET_OS").unwrap();
    let prefix = format!("{out_dir}/tdlib");
    let include_dir = format!("{prefix}/include");
    let lib_dir = format!("{prefix}/lib");
    #[cfg(not(feature = "static"))]
    let dynamic_lib_path = match target_os.as_str() {
        "android" => format!("{lib_dir}/libtdjson.so"),
        "linux" => format!("{lib_dir}/libtdjson.so.{_tdlib_version}"),
        "macos" => format!("{lib_dir}/libtdjson.{_tdlib_version}.dylib"),
        "windows" => format!(r"{lib_dir}\tdjson.lib"),
        _ => panic!("Unsupported target OS: {target_os}"),
    };

    #[cfg(feature = "static")]
    let all_static_libs = native_libraries::static_libraries(&target_os);

    #[cfg(feature = "static")]
    let missing_static_libs: Vec<String> = all_static_libs
        .iter()
        .filter_map(|name| {
            let path = if target_os == "windows" {
                format!(r"{lib_dir}\{name}.lib")
            } else {
                format!("{lib_dir}/lib{name}.a")
            };

            if std::path::PathBuf::from(path.clone()).exists() {
                None
            } else {
                Some(path)
            }
        })
        .collect();

    #[cfg(feature = "static")]
    if !missing_static_libs.is_empty() {
        panic!(
            "required TDLib static libraries not found: {}",
            missing_static_libs.join(", ")
        );
    }

    #[cfg(not(feature = "static"))]
    if !std::path::PathBuf::from(dynamic_lib_path.clone()).exists() {
        panic!("tdjson shared library not found at {dynamic_lib_path}");
    }

    #[cfg(not(feature = "static"))]
    if target_os == "windows" {
        let bin_dir = format!(r"{prefix}\bin");
        println!("cargo:rustc-link-search=native={bin_dir}");
    }

    println!("cargo:rustc-link-search=native={lib_dir}");
    println!("cargo:include={include_dir}");
    #[cfg(feature = "static")]
    for link_name in &all_static_libs {
        println!("cargo:rustc-link-lib=static={link_name}");
    }
    #[cfg(feature = "static")]
    {
        // Link C++ standard library for static tdlib
        if target_os == "linux" || target_os == "macos" {
            println!("cargo:rustc-link-lib=c++");
            if target_os == "linux" {
                println!("cargo:rustc-link-lib=c++abi");
            }
        } else if target_os == "android" {
            println!("cargo:rustc-link-lib=static=c++_static");
        } else if target_os == "windows" {
            // Windows system libraries required by TDLib
            println!("cargo:rustc-link-lib=psapi");
            println!("cargo:rustc-link-lib=Normaliz");
            println!("cargo:rustc-link-lib=Crypt32");
            println!("cargo:rustc-link-lib=advapi32");
            println!("cargo:rustc-link-lib=user32");
            println!("cargo:rustc-link-lib=ws2_32");
            println!("cargo:rustc-link-lib=Mswsock");
            println!("cargo:rustc-link-lib=shell32");
        } else {
            panic!("Unsupported target OS: {target_os}");
        }
    }
    #[cfg(not(feature = "static"))]
    println!("cargo:rustc-link-lib=dylib=tdjson");
    #[cfg(not(feature = "static"))]
    if target_os != "windows" {
        println!("cargo:rustc-link-arg=-Wl,-rpath,{lib_dir}");
    }
}

#[cfg(feature = "download-tdlib")]
fn download_tdlib(tdlib_version: &str) {
    let base_url = concat!(env!("CARGO_PKG_REPOSITORY"), "/releases/download");
    let url = format!(
        "{}/v{}/tdlib-{}-{}-{}.zip",
        base_url,
        env!("CARGO_PKG_VERSION"),
        tdlib_version,
        std::env::var("CARGO_CFG_TARGET_OS").unwrap(),
        std::env::var("CARGO_CFG_TARGET_ARCH").unwrap(),
    );

    let out_dir = env::var("OUT_DIR").unwrap();
    let tdlib_dir = format!("{out_dir}/tdlib");
    let zip_path = format!("{tdlib_dir}.zip");

    // Download a prebuilt tdlib archive using a blocking HTTP client.
    let response = ureq::get(&url).call();

    let mut response = match response {
        Ok(response) => response,
        Err(err) => {
            panic!(
                "Failed to download {url}: {err}\n\
                 The release for this crate version may not exist yet, or \
                 your OS or architecture may be unsupported.\n\
                 Try the `pkg-config` or `local-tdlib` features instead."
            )
        }
    };

    // Create a file to write to
    let mut dest = File::create(&zip_path).unwrap();
    let mut response_reader = response.body_mut().as_reader();
    std::io::copy(&mut response_reader, &mut dest).unwrap();

    let mut archive = zip::ZipArchive::new(File::open(&zip_path).unwrap()).unwrap();

    for i in 0..archive.len() {
        let mut file = archive.by_index(i).unwrap();
        let outpath = Path::new(&out_dir).join(
            file.enclosed_name()
                .expect("TDLib archive contains an invalid entry path"),
        );

        if file.is_dir() {
            std::fs::create_dir_all(&outpath).unwrap();
        } else {
            if let Some(p) = outpath.parent()
                && !p.exists()
            {
                std::fs::create_dir_all(p).unwrap();
            }
            let mut outfile = File::create(&outpath).unwrap();
            std::io::copy(&mut file, &mut outfile).unwrap();
        }

        // Get and set permissions
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if let Some(mode) = file.unix_mode() {
                std::fs::set_permissions(&outpath, std::fs::Permissions::from_mode(mode)).unwrap();
            }
        }
    }

    let _ = std::fs::remove_file(&zip_path);
}

fn main() -> std::io::Result<()> {
    #[cfg(all(feature = "docs", feature = "pkg-config"))]
    compile_error!(
        "feature \"docs\" and feature \"pkg-config\" cannot be enabled at the same time"
    );
    #[cfg(all(feature = "docs", feature = "download-tdlib"))]
    compile_error!(
        "feature \"docs\" and feature \"download-tdlib\" cannot be enabled at the same time"
    );
    #[cfg(all(feature = "pkg-config", feature = "download-tdlib"))]
    compile_error!(
        "feature \"pkg-config\" and feature \"download-tdlib\" cannot be enabled at the same time"
    );
    #[cfg(all(feature = "static", feature = "pkg-config"))]
    compile_error!(
        "feature \"static\" and feature \"pkg-config\" cannot be enabled at the same time"
    );

    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed=Cargo.toml");
    println!("cargo:rerun-if-changed=native-libraries.json");
    let manifest: toml::Table = include_str!("Cargo.toml")
        .parse()
        .expect("invalid Cargo.toml");
    let tdlib_version = manifest["package"]["metadata"]["system-deps"]["tdjson"]
        .as_str()
        .expect("package.metadata.system-deps.tdjson must be a version string");
    println!("cargo:rustc-env=TDLIB_RS_TDLIB_VERSION={tdlib_version}");

    #[cfg(feature = "local-tdlib")]
    println!("cargo:rerun-if-env-changed=LOCAL_TDLIB_PATH");

    // Prevent linking libraries to avoid documentation failure
    #[cfg(not(feature = "docs"))]
    {
        // It requires the following variables to be set:
        // - export PKG_CONFIG_PATH=$HOME/lib/tdlib/lib/pkgconfig/:$PKG_CONFIG_PATH
        // - export LD_LIBRARY_PATH=$HOME/lib/tdlib/lib/:$LD_LIBRARY_PATH
        #[cfg(feature = "pkg-config")]
        system_deps::Config::new().probe().unwrap();

        #[cfg(feature = "download-tdlib")]
        download_tdlib(tdlib_version);

        // It requires the following variable to be set:
        // - export LOCAL_TDLIB_PATH=$HOME/lib/tdlib
        #[cfg(feature = "local-tdlib")]
        copy_local_tdlib();

        #[cfg(any(feature = "download-tdlib", feature = "local-tdlib"))]
        generic_build(tdlib_version);
    }

    let out_dir = env::var("OUT_DIR").unwrap();

    let definitions = load_tl("tl/api.tl")?;

    let mut file = BufWriter::new(File::create(Path::new(&out_dir).join("generated.rs"))?);

    generate_rust_code(&mut file, &definitions, cfg!(feature = "bots-only-api"))?;

    file.flush()?;

    Ok(())
}

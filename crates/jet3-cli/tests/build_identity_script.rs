#![forbid(unsafe_code)]

use std::env;
use std::error::Error;
use std::ffi::OsStr;
use std::fs;
use std::io;
use std::path::Path;
use std::process::{Command, Output};

#[test]
fn redirected_git_environment_cannot_supply_build_identity() -> Result<(), Box<dyn Error>> {
    let temporary = tempfile::tempdir()?;

    let subject_worktree = temporary.path().join("subject");
    let subject_manifest_dir = subject_worktree.join("crates/jet3-cli");
    fs::create_dir_all(&subject_manifest_dir)?;
    fs::copy(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("build.rs"),
        subject_manifest_dir.join("build.rs"),
    )?;
    commit_repository(&subject_worktree, "subject")?;
    let subject_revision = git_text(&subject_worktree, &["rev-parse", "HEAD"])?;

    let foreign_worktree = temporary.path().join("foreign");
    fs::create_dir(&foreign_worktree)?;
    fs::write(
        foreign_worktree.join("foreign.txt"),
        b"foreign repository\n",
    )?;
    commit_repository(&foreign_worktree, "foreign")?;
    let foreign_revision = git_text(&foreign_worktree, &["rev-parse", "HEAD"])?;
    assert_ne!(foreign_revision, subject_revision);

    let executable = temporary
        .path()
        .join(format!("build-identity-script{}", env::consts::EXE_SUFFIX));
    let rustc = env::var_os("RUSTC").unwrap_or_else(|| "rustc".into());
    let build_script = subject_manifest_dir.join("build.rs");
    let compilation = Command::new(rustc)
        .args([
            OsStr::new("--edition=2024"),
            build_script.as_os_str(),
            OsStr::new("-o"),
            executable.as_os_str(),
        ])
        .output()?;
    require_success("compile build script", &compilation)?;

    let clean_identity = run_build_script(&executable, &subject_manifest_dir, &foreign_worktree)?;
    assert_eq!(clean_identity, subject_revision);
    assert!(!clean_identity.starts_with(&foreign_revision));

    fs::write(subject_worktree.join("untracked.txt"), b"untracked\n")?;
    let dirty_identity = run_build_script(&executable, &subject_manifest_dir, &foreign_worktree)?;
    assert_eq!(dirty_identity, format!("{subject_revision}-dirty"));
    Ok(())
}

#[test]
fn cargo_attestation_tracks_nested_untracked_changes_without_clean_rebuilds()
-> Result<(), Box<dyn Error>> {
    let temporary = tempfile::tempdir()?;
    let subject_worktree = temporary.path().join("subject");
    let subject_manifest_dir = subject_worktree.join("crates/jet3-cli");
    let source_dir = subject_manifest_dir.join("src");
    fs::create_dir_all(&source_dir)?;
    fs::copy(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("build.rs"),
        subject_manifest_dir.join("build.rs"),
    )?;
    fs::write(
        subject_manifest_dir.join("Cargo.toml"),
        b"[package]\nname = \"build-identity-cargo-test\"\nversion = \"0.0.0\"\nedition = \"2024\"\n",
    )?;
    fs::write(
        source_dir.join("main.rs"),
        b"fn main() { println!(\"{}\", env!(\"JET3_BUILD_IDENTITY\")); }\n",
    )?;
    fs::write(subject_worktree.join(".gitignore"), b"target/\n")?;
    let target_dir = subject_manifest_dir.join("target");
    cargo(
        &subject_manifest_dir,
        &["generate-lockfile", "--quiet"],
        &target_dir,
    )?;
    commit_repository(&subject_worktree, "subject")?;
    let subject_revision = git_text(&subject_worktree, &["rev-parse", "HEAD"])?;

    assert_eq!(
        cargo_identity(&subject_manifest_dir, &target_dir)?,
        "diagnostic-git-state-unavailable"
    );
    assert_eq!(
        cargo_identity_with_env(&subject_manifest_dir, &target_dir, "v1:mismatch:")?,
        "diagnostic-git-state-unavailable"
    );
    assert_eq!(
        attested_cargo_identity(&subject_manifest_dir, &target_dir)?,
        subject_revision
    );
    assert_attested_fresh(&subject_manifest_dir, &target_dir, &subject_revision)?;

    let nested_dir = subject_worktree.join("generated/nested");
    fs::create_dir_all(&nested_dir)?;
    fs::write(nested_dir.join("untracked.txt"), b"untracked\n")?;
    let dirty_identity = format!("{subject_revision}-dirty");
    assert_eq!(
        attested_cargo_identity(&subject_manifest_dir, &target_dir)?,
        dirty_identity
    );
    assert_attested_fresh(&subject_manifest_dir, &target_dir, &dirty_identity)?;

    fs::remove_dir_all(subject_worktree.join("generated"))?;
    assert_eq!(
        attested_cargo_identity(&subject_manifest_dir, &target_dir)?,
        subject_revision
    );
    assert_attested_fresh(&subject_manifest_dir, &target_dir, &subject_revision)?;
    Ok(())
}

fn assert_attested_fresh(
    manifest_dir: &Path,
    target_dir: &Path,
    expected_identity: &str,
) -> Result<(), Box<dyn Error>> {
    let stable = attested_cargo(manifest_dir, &["run", "--locked", "--verbose"], target_dir)?;
    assert_eq!(String::from_utf8(stable.stdout)?.trim(), expected_identity);
    let stable_log = String::from_utf8(stable.stderr)?;
    assert!(
        stable_log.contains("Fresh build-identity-cargo-test"),
        "unchanged build was not fresh: {stable_log}"
    );
    Ok(())
}

fn attested_cargo_identity(
    manifest_dir: &Path,
    target_dir: &Path,
) -> Result<String, Box<dyn Error>> {
    let output = attested_cargo(manifest_dir, &["run", "--quiet", "--locked"], target_dir)?;
    Ok(String::from_utf8(output.stdout)?.trim().to_owned())
}

fn cargo_identity_with_env(
    manifest_dir: &Path,
    target_dir: &Path,
    attestation: &str,
) -> Result<String, Box<dyn Error>> {
    let output = cargo_command(manifest_dir, &["run", "--quiet", "--locked"], target_dir)
        .env("JET3_BUILD_ATTESTATION_V1", attestation)
        .output()?;
    require_success("run Cargo", &output)?;
    Ok(String::from_utf8(output.stdout)?.trim().to_owned())
}

fn attested_cargo(
    manifest_dir: &Path,
    arguments: &[&str],
    target_dir: &Path,
) -> Result<Output, Box<dyn Error>> {
    let python = env::var_os("PYTHON").unwrap_or_else(|| "python3".into());
    let wrapper = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../tools/cargo_with_build_identity.py")
        .canonicalize()?;
    let cargo = env::var_os("CARGO").unwrap_or_else(|| "cargo".into());
    let mut command = Command::new(python);
    command
        .arg(wrapper)
        .arg("--")
        .arg(cargo)
        .args(arguments)
        .current_dir(manifest_dir)
        .env("CARGO_TERM_COLOR", "never")
        .env("CARGO_TARGET_DIR", target_dir);
    sanitize_git_environment(&mut command);
    let output = command.output()?;
    require_success("run attested Cargo", &output)?;
    Ok(output)
}

fn commit_repository(worktree: &Path, message: &str) -> Result<(), Box<dyn Error>> {
    git(worktree, &["init", "--quiet"])?;
    git(worktree, &["add", "."])?;
    git(
        worktree,
        &[
            "-c",
            "user.name=Build Identity Test",
            "-c",
            "user.email=build-identity@example.invalid",
            "-c",
            "commit.gpgSign=false",
            "commit",
            "--quiet",
            "--message",
            message,
        ],
    )?;
    Ok(())
}

fn run_build_script(
    executable: &Path,
    manifest_dir: &Path,
    foreign_worktree: &Path,
) -> Result<String, Box<dyn Error>> {
    let attestation = worktree_attestation(manifest_dir)?;
    let execution = Command::new(executable)
        .current_dir(foreign_worktree)
        .env("CARGO_MANIFEST_DIR", manifest_dir)
        .env("JET3_BUILD_ATTESTATION_V1", attestation)
        .env("GIT_DIR", foreign_worktree.join(".git"))
        .env("GIT_WORK_TREE", foreign_worktree)
        .output()?;
    require_success("execute build script", &execution)?;
    let stdout = String::from_utf8(execution.stdout)?;
    let identity_prefix = "cargo:rustc-env=JET3_BUILD_IDENTITY=";
    let identities = stdout
        .lines()
        .filter_map(|line| line.strip_prefix(identity_prefix))
        .collect::<Vec<_>>();
    if let [identity] = identities.as_slice() {
        return Ok((*identity).to_owned());
    }
    Err(io::Error::other(format!("unexpected build-script output: {stdout}")).into())
}

fn cargo_identity(manifest_dir: &Path, target_dir: &Path) -> Result<String, Box<dyn Error>> {
    let output = cargo(manifest_dir, &["run", "--quiet", "--locked"], target_dir)?;
    Ok(String::from_utf8(output.stdout)?.trim().to_owned())
}

fn cargo(
    manifest_dir: &Path,
    arguments: &[&str],
    target_dir: impl AsRef<Path>,
) -> Result<Output, Box<dyn Error>> {
    let mut command = cargo_command(manifest_dir, arguments, target_dir.as_ref());
    let output = command.output()?;
    require_success("run Cargo", &output)?;
    Ok(output)
}

fn cargo_command(manifest_dir: &Path, arguments: &[&str], target_dir: &Path) -> Command {
    let cargo = env::var_os("CARGO").unwrap_or_else(|| "cargo".into());
    let mut command = Command::new(cargo);
    command
        .args(arguments)
        .current_dir(manifest_dir)
        .env("CARGO_TERM_COLOR", "never")
        .env("CARGO_TARGET_DIR", target_dir);
    sanitize_git_environment(&mut command);
    command
}

fn worktree_attestation(manifest_dir: &Path) -> Result<String, Box<dyn Error>> {
    let revision = git_text(manifest_dir, &["rev-parse", "--verify", "HEAD"])?;
    let status = git(
        manifest_dir,
        &["status", "--porcelain=v1", "--untracked-files=all"],
    )?
    .stdout;
    Ok(format!("v1:{revision}:{}", hex(&status)))
}

fn hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut result = String::with_capacity(bytes.len().saturating_mul(2));
    for byte in bytes {
        result.push(char::from(HEX[usize::from(byte >> 4)]));
        result.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    result
}

fn git_text(directory: &Path, arguments: &[&str]) -> Result<String, Box<dyn Error>> {
    let output = git(directory, arguments)?;
    Ok(String::from_utf8(output.stdout)?.trim().to_owned())
}

fn git(directory: &Path, arguments: &[&str]) -> Result<Output, Box<dyn Error>> {
    let mut command = Command::new("git");
    command.args(arguments).current_dir(directory);
    sanitize_git_environment(&mut command);
    let output = command.output()?;
    require_success("run Git", &output)?;
    Ok(output)
}

fn sanitize_git_environment(command: &mut Command) {
    for (name, _) in env::vars_os() {
        if name
            .to_string_lossy()
            .to_ascii_uppercase()
            .starts_with("GIT_")
        {
            command.env_remove(name);
        }
    }
}

fn require_success(operation: &str, output: &Output) -> Result<(), Box<dyn Error>> {
    if output.status.success() {
        return Ok(());
    }
    Err(io::Error::other(format!(
        "{operation} failed with {}: {}",
        output.status,
        String::from_utf8_lossy(&output.stderr)
    ))
    .into())
}

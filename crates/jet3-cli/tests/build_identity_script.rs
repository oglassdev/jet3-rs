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
    let execution = Command::new(executable)
        .current_dir(foreign_worktree)
        .env("CARGO_MANIFEST_DIR", manifest_dir)
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

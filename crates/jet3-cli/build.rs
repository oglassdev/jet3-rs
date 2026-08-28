#![forbid(unsafe_code)]

use std::env;
use std::path::{Path, PathBuf};
use std::process::Command;

const NON_GIT_IDENTITY: &str = "diagnostic-non-git-build";
const UNKNOWN_GIT_IDENTITY: &str = "diagnostic-git-state-unavailable";
const GIT_REPOSITORY_ENVIRONMENT: [&str; 9] = [
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_DIR",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_INDEX_FILE",
    "GIT_NAMESPACE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_WORK_TREE",
];

fn main() {
    for variable in GIT_REPOSITORY_ENVIRONMENT {
        println!("cargo:rerun-if-env-changed={variable}");
    }

    let manifest_dir = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap_or_default());
    let Some(worktree) = discover_workspace(&manifest_dir) else {
        println!("cargo:rustc-env=JET3_BUILD_IDENTITY={NON_GIT_IDENTITY}");
        return;
    };
    register_git_inputs(&worktree);

    let Some(revision) = git_text(&worktree, &["rev-parse", "--verify", "HEAD"]) else {
        println!("cargo:rustc-env=JET3_BUILD_IDENTITY={UNKNOWN_GIT_IDENTITY}");
        return;
    };
    if !is_exact_revision(&revision) {
        println!("cargo:rustc-env=JET3_BUILD_IDENTITY={UNKNOWN_GIT_IDENTITY}");
        return;
    }
    let Some(status) = git_output(
        &worktree,
        &["status", "--porcelain=v1", "--untracked-files=all"],
    ) else {
        println!("cargo:rustc-env=JET3_BUILD_IDENTITY={UNKNOWN_GIT_IDENTITY}");
        return;
    };
    let suffix = if status.is_empty() { "" } else { "-dirty" };
    println!("cargo:rustc-env=JET3_BUILD_IDENTITY={revision}{suffix}");
}

fn discover_workspace(manifest_dir: &Path) -> Option<PathBuf> {
    let manifest_dir = std::fs::canonicalize(manifest_dir).ok()?;
    let workspace = manifest_dir
        .ancestors()
        .find(|candidate| candidate.join(".git").exists())?;
    let resolved = git_path(workspace, &["rev-parse", "--show-toplevel"])?;
    let resolved = std::fs::canonicalize(resolved).ok()?;
    (resolved == workspace).then(|| workspace.to_owned())
}

fn register_git_inputs(worktree: &Path) {
    for git_path_name in ["HEAD", "index", "packed-refs"] {
        if let Some(path) = git_path(worktree, &["rev-parse", "--git-path", git_path_name]) {
            println!("cargo:rerun-if-changed={}", path.display());
        }
    }
    if let Some(head_path) = git_path(worktree, &["rev-parse", "--git-path", "HEAD"])
        && let Ok(head) = std::fs::read_to_string(head_path)
        && let Some(reference) = head.trim().strip_prefix("ref: ")
        && let Some(path) = git_path(worktree, &["rev-parse", "--git-path", reference])
    {
        println!("cargo:rerun-if-changed={}", path.display());
    }
    if let Some(files) = git_output(worktree, &["ls-files", "-z"]) {
        for relative in files
            .split(|byte| *byte == 0)
            .filter(|path| !path.is_empty())
        {
            #[cfg(unix)]
            let relative = {
                use std::os::unix::ffi::OsStrExt;
                Path::new(std::ffi::OsStr::from_bytes(relative))
            };
            #[cfg(not(unix))]
            let relative = match std::str::from_utf8(relative) {
                Ok(relative) => Path::new(relative),
                Err(_) => continue,
            };
            println!(
                "cargo:rerun-if-changed={}",
                worktree.join(relative).display()
            );
        }
    }
}

fn git_path(directory: &Path, arguments: &[&str]) -> Option<PathBuf> {
    let value = git_text(directory, arguments)?;
    let path = PathBuf::from(value);
    Some(if path.is_absolute() {
        path
    } else {
        directory.join(path)
    })
}

fn git_text(directory: &Path, arguments: &[&str]) -> Option<String> {
    String::from_utf8(git_output(directory, arguments)?)
        .ok()
        .map(|value| value.trim().to_owned())
}

fn git_output(directory: &Path, arguments: &[&str]) -> Option<Vec<u8>> {
    let mut command = Command::new("git");
    command.args(arguments).current_dir(directory);
    for (name, _) in env::vars_os() {
        if name
            .to_string_lossy()
            .to_ascii_uppercase()
            .starts_with("GIT_")
        {
            command.env_remove(name);
        }
    }
    let output = command.output().ok()?;
    output.status.success().then_some(output.stdout)
}

fn is_exact_revision(value: &str) -> bool {
    value.len() == 40
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

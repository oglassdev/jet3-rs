//! Existing-page publication with exact byte preservation; no page reconstruction.
use crate::{
    ByteOffset, FileSource, PAGE_BYTES, PageNumber, PublishStage, ReadAt, ResourceBudget,
    UpdateError,
};
use std::error::Error as StdError;
use std::io::{Seek, SeekFrom, Write};
use std::path::Path;

pub(crate) struct PageChange<'a> {
    pub page: PageNumber,
    pub before: &'a [u8; PAGE_BYTES],
    pub after: &'a [u8; PAGE_BYTES],
}

pub(crate) fn publish_changes<H, HE>(
    path: &Path,
    mut original: FileSource,
    changes: &[PageChange<'_>],
    budget: &mut ResourceBudget,
    hook: H,
) -> Result<(), UpdateError>
where
    H: FnMut(PublishStage) -> Result<(), HE>,
    HE: StdError + Send + Sync + 'static,
{
    let length = original.len();
    for (index, change) in changes.iter().enumerate() {
        let end = change
            .page
            .get()
            .checked_add(1)
            .and_then(|p| p.checked_mul(PAGE_BYTES as u64))
            .ok_or(UpdateError::Mismatch("page offset"))?;
        if end > length.get()
            || changes[..index]
                .iter()
                .any(|prior| prior.page == change.page)
        {
            return Err(UpdateError::Mismatch("duplicate or absent patch page"));
        }
    }
    crate::atomic::atomic_update_budgeted(
        path,
        budget,
        |file, budget| -> Result<(), UpdateError> {
            for change in changes {
                budget.charge_work_units(PAGE_BYTES as u64)?;
                let mut offset = 0;
                while offset < PAGE_BYTES {
                    if change.before[offset] == change.after[offset] {
                        offset += 1;
                        continue;
                    }
                    let start = offset;
                    while offset < PAGE_BYTES && change.before[offset] != change.after[offset] {
                        offset += 1;
                    }
                    file.seek(SeekFrom::Start(
                        change.page.get() * PAGE_BYTES as u64 + start as u64,
                    ))?;
                    file.write_all(&change.after[start..offset])?;
                }
            }
            Ok(())
        },
        |private, budget| -> Result<(), UpdateError> {
            let mut candidate = FileSource::open(private, budget.read_budget())?;
            if candidate.len() != length {
                return Err(UpdateError::Mismatch("file length"));
            }
            let mut expected = [0; PAGE_BYTES];
            let mut actual = [0; PAGE_BYTES];
            let mut position = 0;
            while position < length.get() {
                let count = (length.get() - position).min(PAGE_BYTES as u64) as usize;
                original.read_exact_at(
                    ByteOffset::new(position),
                    &mut expected[..count],
                    budget.read_budget(),
                )?;
                candidate.read_exact_at(
                    ByteOffset::new(position),
                    &mut actual[..count],
                    budget.read_budget(),
                )?;
                for change in changes {
                    if position == change.page.get() * PAGE_BYTES as u64 {
                        if &expected != change.before {
                            return Err(UpdateError::Mismatch("original page changed"));
                        }
                        expected = *change.after;
                    }
                }
                budget.charge_work_units(count as u64 + changes.len() as u64)?;
                if expected[..count] != actual[..count] {
                    return Err(UpdateError::Mismatch("unrelated or requested bytes"));
                }
                position += count as u64;
            }
            Ok(())
        },
        hook,
    )?;
    Ok(())
}

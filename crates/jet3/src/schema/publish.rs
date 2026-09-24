//! One private file and one verified page journal for a complete schema operation.
use crate::{
    DatabaseReader, FileSource, ResourceBudget, UpdateError, write::page_edits::PageEdits,
};
use std::{cell::Cell, convert::Infallible, fs::File, path::Path};

pub(crate) fn run(
    path: &Path,
    budget: &mut ResourceBudget,
    stage: impl FnOnce(&mut File, &mut PageEdits, &mut ResourceBudget) -> Result<(), UpdateError>,
) -> Result<(), UpdateError> {
    let database = DatabaseReader::open(path, budget)?;
    crate::write::update::require_writable_sort_order(&database)?;
    let journal = Cell::new(Some(PageEdits::new(database.geometry().page_count())));
    let mut original = database.into_source();
    crate::write::atomic::atomic_update_budgeted(
        path,
        budget,
        |file, budget| -> Result<(), UpdateError> {
            let mut combined = journal
                .take()
                .ok_or(UpdateError::Mismatch("schema journal absent"))?;
            stage(file, &mut combined, budget)?;
            journal.set(Some(combined));
            Ok(())
        },
        |private, budget| -> Result<(), UpdateError> {
            let combined = journal
                .take()
                .ok_or(UpdateError::Mismatch("schema journal absent"))?;
            let mut candidate = FileSource::open(private, budget.read_budget())?;
            combined.verify_private(&mut original, &mut candidate, budget)?;
            Ok(())
        },
        |_| Ok::<(), Infallible>(()),
    )?;
    Ok(())
}

pub(crate) fn apply<T>(
    file: &mut File,
    combined: &mut PageEdits,
    budget: &mut ResourceBudget,
    plan: impl FnOnce(
        &mut DatabaseReader<FileSource>,
        &mut ResourceBudget,
    ) -> Result<(PageEdits, T), UpdateError>,
) -> Result<T, UpdateError> {
    let source = FileSource::from_file(file.try_clone()?, budget.read_budget())?;
    let mut database = DatabaseReader::from_source(source, budget)?;
    let (edits, result) = plan(&mut database, budget)?;
    edits.apply_private(&mut database, file, combined, budget)?;
    Ok(result)
}

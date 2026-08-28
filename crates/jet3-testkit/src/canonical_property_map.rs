//! Canonically ordered contiguous property storage.

use std::slice;

use super::TypedValue;

/// A canonically key-ordered property map.
///
/// The contiguous representation makes retained capacity explicit to callers
/// that construct snapshots under an allocation budget. Insertion preserves
/// the ordering and replacement behavior of an ordered map.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct PropertyMap {
    entries: Vec<(String, TypedValue)>,
}

impl PropertyMap {
    /// Constructs an empty property map.
    #[must_use]
    pub const fn new() -> Self {
        Self {
            entries: Vec::new(),
        }
    }

    /// Inserts a value, returning the prior value when the key was present.
    pub fn insert(&mut self, key: String, value: TypedValue) -> Option<TypedValue> {
        match self
            .entries
            .binary_search_by(|(candidate, _)| candidate.cmp(&key))
        {
            Ok(index) => Some(std::mem::replace(&mut self.entries[index].1, value)),
            Err(index) => {
                self.entries.insert(index, (key, value));
                None
            }
        }
    }

    /// Returns the value associated with `key`.
    #[must_use]
    pub fn get(&self, key: &str) -> Option<&TypedValue> {
        self.entries
            .binary_search_by(|(candidate, _)| candidate.as_str().cmp(key))
            .ok()
            .map(|index| &self.entries[index].1)
    }

    /// Returns whether `key` is present.
    #[must_use]
    pub fn contains_key(&self, key: &str) -> bool {
        self.get(key).is_some()
    }

    /// Returns a mutable reference to the value associated with `key`.
    pub fn get_mut(&mut self, key: &str) -> Option<&mut TypedValue> {
        self.entries
            .binary_search_by(|(candidate, _)| candidate.as_str().cmp(key))
            .ok()
            .map(|index| &mut self.entries[index].1)
    }

    /// Removes and returns the first canonical key/value pair.
    pub fn pop_first(&mut self) -> Option<(String, TypedValue)> {
        (!self.entries.is_empty()).then(|| self.entries.remove(0))
    }

    /// Removes all entries while retaining the allocated capacity.
    pub fn clear(&mut self) {
        self.entries.clear();
    }

    /// Returns canonical keys in ascending order.
    pub fn keys(&self) -> impl Iterator<Item = &String> {
        self.entries.iter().map(|(key, _)| key)
    }

    /// Iterates over canonical key/value pairs.
    pub fn iter(&self) -> PropertyIter<'_> {
        PropertyIter(self.entries.iter())
    }

    /// Returns whether the map has no entries.
    #[must_use]
    pub const fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// Returns the entry count.
    #[must_use]
    pub const fn len(&self) -> usize {
        self.entries.len()
    }

    pub(crate) const fn capacity(&self) -> usize {
        self.entries.capacity()
    }

    pub(crate) fn try_reserve_exact(
        &mut self,
        additional: usize,
    ) -> Result<(), std::collections::TryReserveError> {
        self.entries.try_reserve_exact(additional)
    }
}

impl<const N: usize> From<[(String, TypedValue); N]> for PropertyMap {
    fn from(entries: [(String, TypedValue); N]) -> Self {
        entries.into_iter().collect()
    }
}

impl FromIterator<(String, TypedValue)> for PropertyMap {
    fn from_iter<T: IntoIterator<Item = (String, TypedValue)>>(entries: T) -> Self {
        let mut map = Self::new();
        for (key, value) in entries {
            map.insert(key, value);
        }
        map
    }
}

impl<'a> IntoIterator for &'a PropertyMap {
    type Item = (&'a String, &'a TypedValue);
    type IntoIter = PropertyIter<'a>;

    fn into_iter(self) -> Self::IntoIter {
        PropertyIter(self.entries.iter())
    }
}

/// Iterator over a property's canonical key/value pairs.
pub struct PropertyIter<'a>(slice::Iter<'a, (String, TypedValue)>);

impl<'a> Iterator for PropertyIter<'a> {
    type Item = (&'a String, &'a TypedValue);

    fn next(&mut self) -> Option<Self::Item> {
        self.0.next().map(|(key, value)| (key, value))
    }

    fn size_hint(&self) -> (usize, Option<usize>) {
        self.0.size_hint()
    }
}

impl ExactSizeIterator for PropertyIter<'_> {}

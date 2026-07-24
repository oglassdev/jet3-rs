use std::fmt;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::process;

use criterion::{
    BatchSize, BenchmarkId, Criterion, Throughput, black_box, criterion_group, criterion_main,
};
use jet3::{
    BinaryCursor, ByteCount, ByteOffset, Error, FileSource, JET3_PAGE_SIZE, Jet3PageReader,
    PageGeometry, PageNumber, PageOffset, ReadAt, ReadBudget, ReadLimits, ResourceBudget,
    ResourceLimits, SliceSource, jet3_page_geometry, read_jet_signature,
};

const DATASET_SIZES: [usize; 4] = [64, 4 * 1024, 64 * 1024, 1024 * 1024];
const PAGE_COUNTS: [u64; 4] = [1, 16, 1024, 65_536];
const PAGE_SIZE: u64 = 4096;
const JET_HEADER_LENGTH: usize = 19;
const JET_SIGNATURE_LENGTH: u64 = 15;
const STANDARD_JET_HEADER: [u8; JET_HEADER_LENGTH] = *b"\0\0\0\0Standard Jet DB";
const UNKNOWN_JET_HEADER: [u8; JET_HEADER_LENGTH] = *b"\0\0\0\0Not a Jet file!";

fn deterministic_bytes(length: usize) -> Vec<u8> {
    (0..length)
        .map(|index| {
            let index = index as u64;
            index.wrapping_mul(0x9e37_79b9_7f4a_7c15).rotate_left(17) as u8
        })
        .collect()
}

fn limits_for(length: usize, total_multiplier: u64) -> ReadLimits {
    let length = u64::try_from(length).unwrap_or(u64::MAX);
    ReadLimits::new(
        ByteCount::new(length),
        ByteCount::new(length),
        ByteCount::new(length.saturating_mul(total_multiplier)),
    )
}

fn required<T, E>(result: Result<T, E>) -> T
where
    E: fmt::Display,
{
    match result {
        Ok(value) => value,
        Err(error) => {
            eprintln!("benchmark setup or invariant failed: {error}");
            process::abort();
        }
    }
}

#[derive(Debug, Clone, Copy)]
struct GeneratedPageSource {
    length: ByteCount,
}

impl GeneratedPageSource {
    const fn new(length: ByteCount) -> Self {
        Self { length }
    }
}

impl ReadAt for GeneratedPageSource {
    fn len(&self) -> ByteCount {
        self.length
    }

    fn read_exact_at(
        &mut self,
        offset: ByteOffset,
        destination: &mut [u8],
        budget: &mut ReadBudget,
    ) -> Result<(), Error> {
        let count = ByteCount::from_usize(destination.len())?;
        budget.check_input(self.length)?;
        budget.check_read(count)?;
        let end = offset.checked_add(count)?;
        if offset.get() > self.length.get() {
            return Err(Error::OffsetOutOfBounds {
                offset,
                input_len: self.length,
            });
        }
        if end.get() > self.length.get() {
            return Err(Error::UnexpectedEnd {
                offset,
                needed: count,
                available: ByteCount::new(self.length.get().saturating_sub(offset.get())),
            });
        }
        budget.charge_read_attempt(count)?;
        let page_number = offset.get() / JET3_PAGE_SIZE.get();
        destination.fill(page_number.to_le_bytes()[0]);
        Ok(())
    }
}

fn required_page_rejection(result: Result<(), Error>) {
    match result {
        Err(Error::PageOutOfBounds { .. }) => {}
        Ok(()) => {
            eprintln!("benchmark invariant failed: page at count was accepted");
            process::abort();
        }
        Err(error) => {
            eprintln!("benchmark invariant failed: unexpected page rejection: {error}");
            process::abort();
        }
    }
}

fn jet_header_limits() -> ReadLimits {
    ReadLimits::new(
        ByteCount::new(JET_HEADER_LENGTH as u64),
        ByteCount::new(JET_SIGNATURE_LENGTH),
        ByteCount::new(JET_SIGNATURE_LENGTH),
    )
}

fn bench_jet_header(criterion: &mut Criterion) {
    let mut group = criterion.benchmark_group("jet_header");
    let limits = jet_header_limits();

    group.throughput(Throughput::Bytes(JET_SIGNATURE_LENGTH));
    group.bench_function("read_standard_signature", |bencher| {
        bencher.iter_batched(
            || {
                let budget = ReadBudget::new(limits);
                let source = required(SliceSource::new(&STANDARD_JET_HEADER, &budget));
                (source, budget)
            },
            |(mut source, mut budget)| {
                black_box(required(read_jet_signature(&mut source, &mut budget)));
            },
            BatchSize::SmallInput,
        );
    });

    group.throughput(Throughput::Bytes(JET_SIGNATURE_LENGTH));
    group.bench_function("reject_unknown_signature", |bencher| {
        bencher.iter_batched(
            || {
                let budget = ReadBudget::new(limits);
                let source = required(SliceSource::new(&UNKNOWN_JET_HEADER, &budget));
                (source, budget)
            },
            |(mut source, mut budget)| {
                black_box(read_jet_signature(&mut source, &mut budget).is_err());
            },
            BatchSize::SmallInput,
        );
    });

    for page_count in PAGE_COUNTS {
        let source_len = ByteCount::new(page_count.saturating_mul(JET3_PAGE_SIZE.get()));
        let source = GeneratedPageSource::new(source_len);
        group.throughput(Throughput::Elements(1));
        group.bench_with_input(
            BenchmarkId::new("jet3_page_geometry", source_len.get()),
            &source,
            |bencher, source| {
                bencher.iter(|| {
                    black_box(required(jet3_page_geometry(black_box(source))));
                });
            },
        );
    }
    group.finish();
}

fn page_reader_limits(source_len: ByteCount, page_visits: u64) -> ResourceLimits {
    ResourceLimits::new(ReadLimits::new(source_len, JET3_PAGE_SIZE, JET3_PAGE_SIZE))
        .with_max_allocation_bytes(ByteCount::new(0))
        .with_max_decoded_value_bytes(ByteCount::new(0))
        .with_max_total_decoded_bytes(ByteCount::new(0))
        .with_max_item_work(0)
        .with_max_page_visits(page_visits)
        .with_max_chain_depth(0)
        .with_max_total_work_units(page_visits)
}

fn bench_jet3_page_reader(criterion: &mut Criterion) {
    let mut group = criterion.benchmark_group("jet3_page_reader");

    for page_count in PAGE_COUNTS {
        let source_len = ByteCount::new(page_count.saturating_mul(JET3_PAGE_SIZE.get()));
        let last_page = PageNumber::new(page_count.saturating_sub(1));
        let mut success_reader =
            required(Jet3PageReader::new(GeneratedPageSource::new(source_len)));
        let success_limits = page_reader_limits(source_len, 1);
        group.throughput(Throughput::Bytes(JET3_PAGE_SIZE.get()));
        group.bench_with_input(
            BenchmarkId::new("read_last_page", page_count),
            &last_page,
            |bencher, page| {
                bencher.iter_batched(
                    || {
                        (
                            ResourceBudget::new(success_limits),
                            [0_u8; JET3_PAGE_SIZE.get() as usize],
                        )
                    },
                    |(mut budget, mut destination)| {
                        required(success_reader.read_page(
                            black_box(*page),
                            &mut destination,
                            &mut budget,
                        ));
                        black_box(destination);
                    },
                    BatchSize::SmallInput,
                );
            },
        );

        let invalid_page = PageNumber::new(page_count);
        let mut rejection_reader =
            required(Jet3PageReader::new(GeneratedPageSource::new(source_len)));
        let rejection_limits = page_reader_limits(source_len, 0);
        group.throughput(Throughput::Elements(1));
        group.bench_with_input(
            BenchmarkId::new("reject_page_at_count", page_count),
            &invalid_page,
            |bencher, page| {
                bencher.iter_batched(
                    || {
                        (
                            ResourceBudget::new(rejection_limits),
                            [0_u8; JET3_PAGE_SIZE.get() as usize],
                        )
                    },
                    |(mut budget, mut destination)| {
                        required_page_rejection(rejection_reader.read_page(
                            black_box(*page),
                            &mut destination,
                            &mut budget,
                        ));
                        black_box(destination);
                    },
                    BatchSize::SmallInput,
                );
            },
        );
    }
    group.finish();
}

fn bench_binary_cursor(criterion: &mut Criterion) {
    let mut group = criterion.benchmark_group("binary_cursor");

    for size in DATASET_SIZES {
        let data = deterministic_bytes(size);
        let count = ByteCount::new(u64::try_from(size).unwrap_or(u64::MAX));
        let limits = limits_for(size, 1);
        group.throughput(Throughput::Bytes(count.get()));
        group.bench_with_input(BenchmarkId::new("read_exact", size), &size, |bencher, _| {
            bencher.iter_batched(
                || ReadBudget::new(limits),
                |mut budget| {
                    let mut cursor = required(BinaryCursor::new(&data, &mut budget));
                    black_box(required(cursor.read_exact(count)).len());
                },
                BatchSize::SmallInput,
            );
        });

        group.throughput(Throughput::Bytes(count.get()));
        group.bench_with_input(BenchmarkId::new("u32_scan", size), &size, |bencher, _| {
            let words = size / size_of::<u32>();
            bencher.iter_batched(
                || ReadBudget::new(limits),
                |mut budget| {
                    let mut cursor = required(BinaryCursor::new(&data, &mut budget));
                    for _ in 0..words {
                        black_box(required(cursor.read_u32_le()));
                    }
                },
                BatchSize::SmallInput,
            );
        });

        group.throughput(Throughput::Elements(1));
        group.bench_with_input(
            BenchmarkId::new("reject_one_byte_past_end", size),
            &size,
            |bencher, _| {
                let oversized = ByteCount::new(count.get().saturating_add(1));
                bencher.iter_batched(
                    || ReadBudget::new(ReadLimits::new(oversized, oversized, oversized)),
                    |mut budget| {
                        let mut cursor = required(BinaryCursor::new(&data, &mut budget));
                        black_box(cursor.read_exact(oversized).is_err());
                    },
                    BatchSize::SmallInput,
                );
            },
        );
    }
    group.finish();
}

fn bench_slice_source(criterion: &mut Criterion) {
    let mut group = criterion.benchmark_group("slice_source");

    for size in DATASET_SIZES {
        let data = deterministic_bytes(size);
        let limits = limits_for(size, 1);
        let count = ByteCount::new(u64::try_from(size).unwrap_or(u64::MAX));
        group.throughput(Throughput::Bytes(count.get()));

        group.bench_with_input(
            BenchmarkId::new("read_exact_at", size),
            &size,
            |bencher, _| {
                bencher.iter_batched(
                    || {
                        let budget = ReadBudget::new(limits);
                        let source = required(SliceSource::new(&data, &budget));
                        (source, budget, vec![0_u8; size])
                    },
                    |(mut source, mut budget, mut destination)| {
                        required(source.read_exact_at(
                            ByteOffset::new(0),
                            &mut destination,
                            &mut budget,
                        ));
                        black_box(destination.as_slice());
                    },
                    BatchSize::LargeInput,
                );
            },
        );
    }
    group.finish();
}

fn bench_file_source(criterion: &mut Criterion) {
    let mut group = criterion.benchmark_group("file_source");

    for size in DATASET_SIZES {
        let data = deterministic_bytes(size);
        let path = write_dataset_file(&data, size);
        let limits = limits_for(size, 1);
        let count = ByteCount::new(u64::try_from(size).unwrap_or(u64::MAX));
        group.throughput(Throughput::Bytes(count.get()));

        group.bench_with_input(
            BenchmarkId::new("read_exact_at", size),
            &size,
            |bencher, _| {
                bencher.iter_batched(
                    || {
                        let budget = ReadBudget::new(limits);
                        let source = required(FileSource::open(&path, &budget));
                        (source, budget, vec![0_u8; size])
                    },
                    |(mut source, mut budget, mut destination)| {
                        required(source.read_exact_at(
                            ByteOffset::new(0),
                            &mut destination,
                            &mut budget,
                        ));
                        black_box(destination.as_slice());
                    },
                    BatchSize::LargeInput,
                );
            },
        );

        if let Err(error) = fs::remove_file(&path) {
            eprintln!(
                "failed to remove benchmark dataset {}: {error}",
                path.display()
            );
            process::abort();
        }
    }
    group.finish();
}

fn write_dataset_file(data: &[u8], size: usize) -> PathBuf {
    let mut path = std::env::temp_dir();
    path.push(format!(
        "jet3-format-benchmark-{}-{size}.bin",
        process::id()
    ));

    match fs::remove_file(&path) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => {
            eprintln!(
                "failed to remove stale benchmark dataset {}: {error}",
                path.display()
            );
            process::abort();
        }
    }

    let file = OpenOptions::new().create_new(true).write(true).open(&path);
    let mut file = match file {
        Ok(file) => file,
        Err(error) => {
            eprintln!(
                "failed to create benchmark dataset {}: {error}",
                path.display()
            );
            process::abort();
        }
    };
    if let Err(error) = file.write_all(data).and_then(|()| file.sync_all()) {
        eprintln!(
            "failed to write benchmark dataset {}: {error}",
            path.display()
        );
        process::abort();
    }
    path
}

fn bench_page_geometry(criterion: &mut Criterion) {
    let mut group = criterion.benchmark_group("page_geometry");

    for page_count in PAGE_COUNTS {
        let source_len = ByteCount::new(page_count.saturating_mul(PAGE_SIZE));
        let geometry = required(PageGeometry::new(source_len, ByteCount::new(PAGE_SIZE)));
        let last_page = PageNumber::new(page_count.saturating_sub(1));
        let last_byte = PageOffset::new(PAGE_SIZE - 1);
        group.throughput(Throughput::Elements(1));

        group.bench_with_input(
            BenchmarkId::new("map_last_page", page_count),
            &page_count,
            |bencher, _| {
                bencher.iter(|| {
                    black_box(required(
                        geometry.byte_offset(black_box(last_page), black_box(last_byte)),
                    ));
                });
            },
        );

        group.throughput(Throughput::Elements(1));
        group.bench_with_input(
            BenchmarkId::new("reject_page_at_count", page_count),
            &page_count,
            |bencher, _| {
                let invalid = PageNumber::new(page_count);
                bencher.iter(|| {
                    black_box(geometry.page_byte_range(black_box(invalid)).is_err());
                });
            },
        );
    }
    group.finish();
}

fn bench_read_budget(criterion: &mut Criterion) {
    let mut group = criterion.benchmark_group("read_budget");

    for operations in PAGE_COUNTS {
        let limits = ReadLimits::new(
            ByteCount::new(operations),
            ByteCount::new(operations),
            ByteCount::new(operations),
        );
        group.throughput(Throughput::Elements(operations));

        group.bench_with_input(
            BenchmarkId::new("charge_one_byte", operations),
            &operations,
            |bencher, _| {
                bencher.iter_batched(
                    || ReadBudget::new(limits),
                    |mut budget| {
                        for _ in 0..operations {
                            required(budget.charge_read_attempt(ByteCount::new(1)));
                        }
                        black_box(budget.total_read());
                    },
                    BatchSize::SmallInput,
                );
            },
        );

        group.throughput(Throughput::Elements(1));
        group.bench_with_input(
            BenchmarkId::new("reject_after_exhaustion", operations),
            &operations,
            |bencher, _| {
                bencher.iter_batched(
                    || {
                        let mut budget = ReadBudget::new(limits);
                        required(budget.charge_read_attempt(ByteCount::new(operations)));
                        budget
                    },
                    |mut budget| {
                        black_box(budget.charge_read_attempt(ByteCount::new(1)).is_err());
                    },
                    BatchSize::SmallInput,
                );
            },
        );
    }
    group.finish();
}

fn resource_limits(operations: u64) -> ResourceLimits {
    ResourceLimits::new(ReadLimits::new(
        ByteCount::new(0),
        ByteCount::new(0),
        ByteCount::new(0),
    ))
    .with_max_allocation_bytes(ByteCount::new(operations))
    .with_max_decoded_value_bytes(ByteCount::new(operations))
    .with_max_total_decoded_bytes(ByteCount::new(operations))
    .with_max_item_work(operations)
    .with_max_page_visits(operations)
    .with_max_chain_depth(operations)
    .with_max_total_work_units(operations)
}

fn bench_resource_budget(criterion: &mut Criterion) {
    let mut group = criterion.benchmark_group("resource_budget");

    for operations in PAGE_COUNTS {
        let limits = resource_limits(operations);
        group.throughput(Throughput::Elements(operations));

        group.bench_with_input(
            BenchmarkId::new("charge_mixed_units", operations),
            &operations,
            |bencher, _| {
                bencher.iter_batched(
                    || ResourceBudget::new(limits),
                    |mut budget| {
                        for index in 0..operations {
                            match index % 4 {
                                0 => required(budget.charge_allocation(ByteCount::new(1))),
                                1 => required(budget.charge_decoded_value(ByteCount::new(1))),
                                2 => required(budget.charge_items(1)),
                                _ => required(budget.charge_page_visits(1)),
                            }
                        }
                        black_box(budget.total_work_units());
                    },
                    BatchSize::SmallInput,
                );
            },
        );

        group.throughput(Throughput::Elements(1));
        group.bench_with_input(
            BenchmarkId::new("reject_after_work_exhaustion", operations),
            &operations,
            |bencher, _| {
                bencher.iter_batched(
                    || {
                        let mut budget = ResourceBudget::new(limits);
                        required(budget.charge_work_units(operations));
                        budget
                    },
                    |mut budget| {
                        black_box(budget.charge_work_units(1).is_err());
                    },
                    BatchSize::SmallInput,
                );
            },
        );
    }
    group.finish();
}

criterion_group!(
    benches,
    bench_jet_header,
    bench_jet3_page_reader,
    bench_binary_cursor,
    bench_slice_source,
    bench_file_source,
    bench_page_geometry,
    bench_read_budget,
    bench_resource_budget
);
criterion_main!(benches);

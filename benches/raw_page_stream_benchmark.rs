use std::process;

use criterion::{BatchSize, BenchmarkId, Criterion, Throughput, black_box};
use jet3::{
    ByteCount, JET3_PAGE_SIZE, RawJet3Candidate, ReadLimits, ResourceBudget, ResourceLimits,
};

use super::{GeneratedSource, candidate_inspect_limits, required};

const STREAM_PAGE_COUNTS: [u64; 3] = [1, 16, 1024];

fn page_stream_limits(source_len: ByteCount, page_count: u64) -> ResourceLimits {
    ResourceLimits::new(ReadLimits::new(source_len, JET3_PAGE_SIZE, source_len))
        .with_max_allocation_bytes(ByteCount::new(0))
        .with_max_decoded_value_bytes(ByteCount::new(0))
        .with_max_total_decoded_bytes(ByteCount::new(0))
        .with_max_item_work(0)
        .with_max_page_visits(page_count)
        .with_max_chain_depth(0)
        .with_max_total_work_units(page_count)
}

fn require_complete_stream(
    pages_read: u64,
    page_count: u64,
    source_len: ByteCount,
    budget: &mut ResourceBudget,
) {
    if pages_read != page_count
        || budget.read_budget().total_read() != source_len
        || budget.page_visits() != page_count
        || budget.total_work_units() != page_count
    {
        eprintln!("benchmark invariant failed: raw page stream accounting was incomplete");
        process::abort();
    }
}

pub(super) fn bench_raw_page_stream(criterion: &mut Criterion) {
    let mut group = criterion.benchmark_group("raw_page_stream");

    for page_count in STREAM_PAGE_COUNTS {
        let source_len = ByteCount::new(page_count.saturating_mul(JET3_PAGE_SIZE.get()));
        let source = GeneratedSource::candidate(source_len);
        let mut candidate = required(RawJet3Candidate::inspect(
            source,
            &mut ResourceBudget::new(candidate_inspect_limits(source_len)),
        ));
        let limits = page_stream_limits(source_len, page_count);
        group.throughput(Throughput::Bytes(source_len.get()));
        group.bench_with_input(
            BenchmarkId::new("stream_all_raw_pages", page_count),
            &page_count,
            |bencher, page_count| {
                bencher.iter_batched(
                    || ResourceBudget::new(limits),
                    |mut budget| {
                        let mut cursor = candidate.raw_pages();
                        while let Some(page) = required(cursor.next_page(&mut budget)) {
                            black_box(page.bytes());
                        }
                        let pages_read = cursor.pages_read();
                        require_complete_stream(pages_read, *page_count, source_len, &mut budget);
                        black_box(pages_read);
                    },
                    BatchSize::SmallInput,
                );
            },
        );
    }
    group.finish();
}

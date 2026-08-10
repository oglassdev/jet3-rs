use std::fmt;
use std::process;

use criterion::{BatchSize, BenchmarkId, Criterion, Throughput, criterion_group, criterion_main};
use jet3::{BinaryWriter, ByteCount, ByteOffset, ReadLimits, ResourceBudget, ResourceLimits};

const OUTPUT_SIZES: [usize; 4] = [64, 4 * 1024, 64 * 1024, 1024 * 1024];
const WORD_BYTES: usize = size_of::<u32>();
const WORD_MULTIPLIER: u32 = 0x9e37_79b9;
const WORD_MASK: u32 = 0xa5c3_1f27;

struct WriterState {
    position: ByteOffset,
    remaining: ByteCount,
    total_encoded: ByteCount,
}

struct BenchmarkOutput {
    output: Vec<u8>,
    budget: ResourceBudget,
    state: WriterState,
}

fn required<T, E>(result: Result<T, E>) -> T
where
    E: fmt::Display,
{
    match result {
        Ok(value) => value,
        Err(error) => {
            eprintln!("binary-writer benchmark invariant failed: {error}");
            process::abort();
        }
    }
}

fn required_count(value: usize) -> u64 {
    match u64::try_from(value) {
        Ok(count) => count,
        Err(error) => {
            eprintln!("binary-writer benchmark size conversion failed: {error}");
            process::abort();
        }
    }
}

fn required_double(value: u64) -> u64 {
    match value.checked_mul(2) {
        Some(doubled) => doubled,
        None => {
            eprintln!("binary-writer benchmark byte count overflowed");
            process::abort();
        }
    }
}

fn required_word_count(output_size: usize) -> usize {
    if !output_size.is_multiple_of(WORD_BYTES) {
        eprintln!("binary-writer benchmark output is not word-aligned");
        process::abort();
    }
    output_size / WORD_BYTES
}

fn required_output_bytes(word_count: usize) -> usize {
    match word_count.checked_mul(WORD_BYTES) {
        Some(bytes) => bytes,
        None => {
            eprintln!("binary-writer benchmark output length overflowed");
            process::abort();
        }
    }
}

fn exact_budget(encoded_bytes: u64) -> ResourceBudget {
    ResourceBudget::new(
        ResourceLimits::new(ReadLimits::default())
            .with_max_encoded_bytes(ByteCount::new(encoded_bytes))
            .with_max_total_work_units(encoded_bytes),
    )
}

fn deterministic_word(index: usize, inverted: bool) -> u32 {
    let index = required(u32::try_from(index));
    let word = index.wrapping_mul(WORD_MULTIPLIER).rotate_left(13) ^ WORD_MASK;
    if inverted { !word } else { word }
}

fn precomputed_words(word_count: usize, inverted: bool) -> Vec<u32> {
    (0..word_count)
        .map(|index| deterministic_word(index, inverted))
        .collect()
}

fn encode_words(writer: &mut BinaryWriter<'_, '_>, words: &[u32]) {
    for word in words {
        required(writer.write_u32_le(*word));
    }
}

fn writer_state(writer: &BinaryWriter<'_, '_>) -> WriterState {
    WriterState {
        position: writer.position(),
        remaining: required(writer.remaining()),
        total_encoded: writer.total_encoded(),
    }
}

fn write_once(mut output: Vec<u8>, mut budget: ResourceBudget, words: &[u32]) -> BenchmarkOutput {
    let state = {
        let mut writer = required(BinaryWriter::new(&mut output, &mut budget));
        encode_words(&mut writer, words);
        writer_state(&writer)
    };
    BenchmarkOutput {
        output,
        budget,
        state,
    }
}

fn rewrite(
    mut output: Vec<u8>,
    mut budget: ResourceBudget,
    first_words: &[u32],
    rewrite_words: &[u32],
) -> BenchmarkOutput {
    let state = {
        let mut writer = required(BinaryWriter::new(&mut output, &mut budget));
        encode_words(&mut writer, first_words);
        required(writer.seek(ByteOffset::new(0)));
        encode_words(&mut writer, rewrite_words);
        writer_state(&writer)
    };
    BenchmarkOutput {
        output,
        budget,
        state,
    }
}

fn verify_preflight(result: &BenchmarkOutput, expected_words: &[u32], expected_encoded: u64) {
    let expected_position = required_count(result.output.len());
    if result.state.position != ByteOffset::new(expected_position)
        || result.state.remaining != ByteCount::new(0)
        || result.state.total_encoded != ByteCount::new(expected_encoded)
        || result.budget.encoded_bytes() != ByteCount::new(expected_encoded)
        || result.budget.total_work_units() != expected_encoded
    {
        eprintln!(
            "binary-writer benchmark accounting mismatch: position={}, remaining={}, \
             writer_encoded={}, budget_encoded={}, total_work={}",
            result.state.position.get(),
            result.state.remaining.get(),
            result.state.total_encoded.get(),
            result.budget.encoded_bytes().get(),
            result.budget.total_work_units()
        );
        process::abort();
    }

    if result.output.len() != required_output_bytes(expected_words.len()) {
        eprintln!("binary-writer benchmark output length mismatch");
        process::abort();
    }
    for (actual, expected) in result.output.chunks_exact(WORD_BYTES).zip(expected_words) {
        if actual != expected.to_le_bytes() {
            eprintln!("binary-writer benchmark output bytes mismatch");
            process::abort();
        }
    }
}

fn bench_binary_writer(criterion: &mut Criterion) {
    let mut group = criterion.benchmark_group("binary_writer");

    for output_size in OUTPUT_SIZES {
        let output_bytes = required_count(output_size);
        let word_count = required_word_count(output_size);
        let first_words = precomputed_words(word_count, false);
        let rewrite_words = precomputed_words(word_count, true);
        let rewrite_bytes = required_double(output_bytes);

        let first_preflight = write_once(
            vec![0xa5_u8; output_size],
            exact_budget(output_bytes),
            &first_words,
        );
        verify_preflight(&first_preflight, &first_words, output_bytes);
        drop(first_preflight);

        let rewrite_preflight = rewrite(
            vec![0xa5_u8; output_size],
            exact_budget(rewrite_bytes),
            &first_words,
            &rewrite_words,
        );
        verify_preflight(&rewrite_preflight, &rewrite_words, rewrite_bytes);
        drop(rewrite_preflight);

        group.throughput(Throughput::Bytes(output_bytes));
        group.bench_with_input(
            BenchmarkId::new("write_u32_le", output_size),
            &output_size,
            |bencher, size| {
                bencher.iter_batched(
                    || (vec![0xa5_u8; *size], exact_budget(required_count(*size))),
                    |(output, budget)| write_once(output, budget, &first_words),
                    BatchSize::LargeInput,
                );
            },
        );

        group.throughput(Throughput::Bytes(rewrite_bytes));
        group.bench_with_input(
            BenchmarkId::new("rewrite_u32_le", output_size),
            &output_size,
            |bencher, size| {
                bencher.iter_batched(
                    || {
                        (
                            vec![0xa5_u8; *size],
                            exact_budget(required_double(required_count(*size))),
                        )
                    },
                    |(output, budget)| rewrite(output, budget, &first_words, &rewrite_words),
                    BatchSize::LargeInput,
                );
            },
        );
    }

    group.finish();
}

criterion_group!(benches, bench_binary_writer);
criterion_main!(benches);

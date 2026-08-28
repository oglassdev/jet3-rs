//! Small safe SHA-256 implementation for protocol identity (`SRC-0027`).

use std::error::Error;
use std::fmt;

const INITIAL: [u32; 8] = [
    0x6a09_e667,
    0xbb67_ae85,
    0x3c6e_f372,
    0xa54f_f53a,
    0x510e_527f,
    0x9b05_688c,
    0x1f83_d9ab,
    0x5be0_cd19,
];

const ROUND: [u32; 64] = [
    0x428a_2f98,
    0x7137_4491,
    0xb5c0_fbcf,
    0xe9b5_dba5,
    0x3956_c25b,
    0x59f1_11f1,
    0x923f_82a4,
    0xab1c_5ed5,
    0xd807_aa98,
    0x1283_5b01,
    0x2431_85be,
    0x550c_7dc3,
    0x72be_5d74,
    0x80de_b1fe,
    0x9bdc_06a7,
    0xc19b_f174,
    0xe49b_69c1,
    0xefbe_4786,
    0x0fc1_9dc6,
    0x240c_a1cc,
    0x2de9_2c6f,
    0x4a74_84aa,
    0x5cb0_a9dc,
    0x76f9_88da,
    0x983e_5152,
    0xa831_c66d,
    0xb003_27c8,
    0xbf59_7fc7,
    0xc6e0_0bf3,
    0xd5a7_9147,
    0x06ca_6351,
    0x1429_2967,
    0x27b7_0a85,
    0x2e1b_2138,
    0x4d2c_6dfc,
    0x5338_0d13,
    0x650a_7354,
    0x766a_0abb,
    0x81c2_c92e,
    0x9272_2c85,
    0xa2bf_e8a1,
    0xa81a_664b,
    0xc24b_8b70,
    0xc76c_51a3,
    0xd192_e819,
    0xd699_0624,
    0xf40e_3585,
    0x106a_a070,
    0x19a4_c116,
    0x1e37_6c08,
    0x2748_774c,
    0x34b0_bcb5,
    0x391c_0cb3,
    0x4ed8_aa4a,
    0x5b9c_ca4f,
    0x682e_6ff3,
    0x748f_82ee,
    0x78a5_636f,
    0x84c8_7814,
    0x8cc7_0208,
    0x90be_fffa,
    0xa450_6ceb,
    0xbef9_a3f7,
    0xc671_78f2,
];

/// SHA-256 inputs must be shorter than 2^64 bits.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Sha256LengthError;

impl fmt::Display for Sha256LengthError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("SHA-256 input length exceeds the 64-bit bit-length field")
    }
}

impl Error for Sha256LengthError {}

/// Incremental, allocation-free SHA-256 state.
#[derive(Clone, Debug)]
pub struct Sha256Hasher {
    state: [u32; 8],
    block: [u8; 64],
    block_len: usize,
    byte_len: u64,
}

impl Default for Sha256Hasher {
    fn default() -> Self {
        Self::new()
    }
}

impl Sha256Hasher {
    /// Starts an empty SHA-256 computation.
    #[must_use]
    pub const fn new() -> Self {
        Self {
            state: INITIAL,
            block: [0; 64],
            block_len: 0,
            byte_len: 0,
        }
    }

    /// Adds bytes, rejecting lengths that cannot fit SHA-256's bit counter.
    pub fn update(&mut self, mut input: &[u8]) -> Result<(), Sha256LengthError> {
        let added = u64::try_from(input.len()).map_err(|_| Sha256LengthError)?;
        self.byte_len = self
            .byte_len
            .checked_add(added)
            .filter(|length| *length <= u64::MAX / 8)
            .ok_or(Sha256LengthError)?;
        if self.block_len != 0 {
            let count = (64 - self.block_len).min(input.len());
            self.block[self.block_len..self.block_len + count].copy_from_slice(&input[..count]);
            self.block_len += count;
            input = &input[count..];
            if self.block_len < 64 {
                return Ok(());
            }
            compress(&mut self.state, &self.block);
            self.block_len = 0;
        }
        while input.len() >= 64 {
            let block: &[u8; 64] = input[..64].try_into().map_err(|_| Sha256LengthError)?;
            compress(&mut self.state, block);
            input = &input[64..];
        }
        self.block[..input.len()].copy_from_slice(input);
        self.block_len = input.len();
        Ok(())
    }

    /// Finishes the digest without allocating.
    pub fn finalize(mut self) -> Result<[u8; 32], Sha256LengthError> {
        let bit_len = self.byte_len.checked_mul(8).ok_or(Sha256LengthError)?;
        self.block[self.block_len] = 0x80;
        self.block_len += 1;
        if self.block_len > 56 {
            self.block[self.block_len..].fill(0);
            compress(&mut self.state, &self.block);
            self.block = [0; 64];
        } else {
            self.block[self.block_len..56].fill(0);
        }
        self.block[56..].copy_from_slice(&bit_len.to_be_bytes());
        compress(&mut self.state, &self.block);
        let mut digest = [0_u8; 32];
        for (word, output) in self.state.iter().zip(digest.chunks_exact_mut(4)) {
            output.copy_from_slice(&word.to_be_bytes());
        }
        Ok(digest)
    }
}

/// Returns lowercase SHA-256 text for one complete byte slice.
pub fn sha256_hex(input: &[u8]) -> Result<String, Sha256LengthError> {
    let mut hasher = Sha256Hasher::new();
    hasher.update(input)?;
    Ok(hex_digest(hasher.finalize()?))
}

/// Renders a digest as exactly 64 lowercase hexadecimal digits.
#[must_use]
pub fn hex_digest(digest: [u8; 32]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(64);
    for byte in digest {
        output.push(char::from(DIGITS[usize::from(byte >> 4)]));
        output.push(char::from(DIGITS[usize::from(byte & 0x0f)]));
    }
    output
}

fn compress(state: &mut [u32; 8], block: &[u8; 64]) {
    let mut schedule = [0_u32; 64];
    for (word, bytes) in schedule.iter_mut().zip(block.chunks_exact(4)) {
        *word = u32::from_be_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
    }
    for index in 16..64 {
        let s0 = schedule[index - 15].rotate_right(7)
            ^ schedule[index - 15].rotate_right(18)
            ^ (schedule[index - 15] >> 3);
        let s1 = schedule[index - 2].rotate_right(17)
            ^ schedule[index - 2].rotate_right(19)
            ^ (schedule[index - 2] >> 10);
        schedule[index] = schedule[index - 16]
            .wrapping_add(s0)
            .wrapping_add(schedule[index - 7])
            .wrapping_add(s1);
    }
    let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut h] = *state;
    for (constant, word) in ROUND.into_iter().zip(schedule) {
        let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
        let choice = (e & f) ^ (!e & g);
        let first = h
            .wrapping_add(s1)
            .wrapping_add(choice)
            .wrapping_add(constant)
            .wrapping_add(word);
        let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
        let majority = (a & b) ^ (a & c) ^ (b & c);
        let second = s0.wrapping_add(majority);
        h = g;
        g = f;
        f = e;
        e = d.wrapping_add(first);
        d = c;
        c = b;
        b = a;
        a = first.wrapping_add(second);
    }
    for (target, value) in state.iter_mut().zip([a, b, c, d, e, f, g, h]) {
        *target = target.wrapping_add(value);
    }
}

#[cfg(test)]
mod tests {
    use super::{Sha256Hasher, hex_digest, sha256_hex};

    fn patterned_bytes(length: usize) -> Vec<u8> {
        (0..length)
            .map(|index| ((index * 37 + 11) % 256) as u8)
            .collect()
    }

    fn incremental_digest(
        input: &[u8],
        chunks: &[usize],
    ) -> Result<String, Box<dyn std::error::Error>> {
        let mut hasher = Sha256Hasher::new();
        let mut offset = 0;
        for &chunk in chunks {
            let end = offset + chunk;
            hasher.update(&input[offset..end])?;
            offset = end;
        }
        assert_eq!(offset, input.len());
        Ok(hex_digest(hasher.finalize()?))
    }

    #[test]
    fn standard_empty_and_abc_vectors_match() -> Result<(), Box<dyn std::error::Error>> {
        assert_eq!(
            sha256_hex(b"")?,
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(
            sha256_hex(b"abc")?,
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        Ok(())
    }

    #[test]
    fn split_updates_match_padding_boundary_vectors() -> Result<(), Box<dyn std::error::Error>> {
        let vectors = [
            (
                55,
                "2900465fcb533e05a158fd2b3be0e5e3b03740d83060aa3580e0d98a96bf2384",
            ),
            (
                56,
                "31454ff48ef36af2f08fd511bdc37d9d5855ac23e992e5ff5445cb6b7674a674",
            ),
            (
                57,
                "bcc0a5d3791b985b7550e04ca660a6c63a589ba1edd2283c8e110e5b515df124",
            ),
            (
                58,
                "625f50f0c121a43afb524b104e3edf8eacf001ffd8795ac11609f458bb4c9003",
            ),
            (
                59,
                "5a85bd878ca7ff9e9a89748f613bf443cf10d199662c21e7115fca98262fa411",
            ),
            (
                60,
                "35d6f8129baac2bc4427ae4f5d831acde4a59233146da0e0524cd6b445ff6982",
            ),
            (
                61,
                "de1025bf69990152626ae709c870a15a907a1775ecf669fb3d4955a4ee23a3da",
            ),
            (
                62,
                "88908d0c7953bf0924d1e1e6f494578300aab9c32e4312f1e733832ff57d8bff",
            ),
            (
                63,
                "5f6401b96532c36de4e65beec0409b69b1d181864c8009b7a04f43e5d56350d1",
            ),
            (
                64,
                "94eb5de4943613fd048dc93393ab06877405faa39c11f53e9386083339833e7e",
            ),
            (
                65,
                "fc518669b6eb4b4dd91827ecacef86689c725bd5bab888fd3b26dbb196eec954",
            ),
        ];
        for (length, expected) in vectors {
            let input = patterned_bytes(length);
            for split in 0..=length {
                let mut hasher = Sha256Hasher::new();
                hasher.update(&input[..split])?;
                hasher.update(&[])?;
                hasher.update(&input[split..])?;
                assert_eq!(hex_digest(hasher.finalize()?), expected);
            }
        }
        Ok(())
    }

    #[test]
    fn deterministic_random_chunking_matches_independent_digests()
    -> Result<(), Box<dyn std::error::Error>> {
        let vectors = [
            (
                0,
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            ),
            (
                1,
                "e7cf46a078fed4fafd0b5e3aff144802b853f8ae459a4f0c14add3314b7cc3a6",
            ),
            (
                63,
                "5f6401b96532c36de4e65beec0409b69b1d181864c8009b7a04f43e5d56350d1",
            ),
            (
                64,
                "94eb5de4943613fd048dc93393ab06877405faa39c11f53e9386083339833e7e",
            ),
            (
                65,
                "fc518669b6eb4b4dd91827ecacef86689c725bd5bab888fd3b26dbb196eec954",
            ),
            (
                127,
                "0fe729ff19257bd6fec853acc2ea355f6b34b58e6c0f684c3e188fcdfcd9baae",
            ),
            (
                128,
                "0aedd4856f8eba0963627336ad5144a9a7dbe12498e6066f0165fc97d8ddee4c",
            ),
            (
                129,
                "4f1757ae4bffbae86d775b831765b75af154d52f7deaa46dd378051a2d3ad57f",
            ),
            (
                1024,
                "ffbad8f947474cfdd5b2bb22d7e0bf5ee8ba2b7af859d0c2bb28622db6a4be47",
            ),
        ];
        for (length, expected) in vectors {
            let input = patterned_bytes(length);
            let mut hasher = Sha256Hasher::new();
            let mut state = 0x6d2b_79f5_u32 ^ length as u32;
            let mut offset = 0;
            hasher.update(&[])?;
            while offset < input.len() {
                state ^= state << 13;
                state ^= state >> 17;
                state ^= state << 5;
                let chunk = (state as usize % 23 + 1).min(input.len() - offset);
                hasher.update(&input[offset..offset + chunk])?;
                hasher.update(&[])?;
                offset += chunk;
            }
            hasher.update(&[])?;
            assert_eq!(hex_digest(hasher.finalize()?), expected);
        }
        Ok(())
    }

    #[test]
    fn multiple_short_updates_survive_a_block_boundary() -> Result<(), Box<dyn std::error::Error>> {
        let input = patterned_bytes(70);
        assert_eq!(
            incremental_digest(&input, &[3, 5, 7, 11, 13, 17, 2, 4, 3, 2, 3])?,
            "54600d51dc1bbf04fb01cd5120f7797e4f5b974e224c8963865d1ecad1bf6d3c"
        );
        Ok(())
    }
}

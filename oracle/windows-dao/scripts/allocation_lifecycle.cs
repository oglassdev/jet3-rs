using System;
using System.Security.Cryptography;

public static class AllocationRows {
    public static string Pad(int seed, int column) { return new string((char)(65 + (seed + 3 * column) % 26), 255); }
    public static string Memo(int seed) { return new string((char)(65 + seed % 26), 1800); }
    public static byte[] Ole(int seed) {
        var value = new byte[1800];
        for (int i = 0; i < value.Length; i++) value[i] = (byte)((i * 37 + seed) % 256);
        return value;
    }
    public static void Feed(HashAlgorithm hash, byte[] value) {
        var size = BitConverter.GetBytes(value == null ? -1 : value.Length);
        hash.TransformBlock(size, 0, size.Length, size, 0);
        if (value != null) hash.TransformBlock(value, 0, value.Length, value, 0);
    }
    public static string Finish(HashAlgorithm hash) {
        hash.TransformFinalBlock(new byte[0], 0, 0);
        return BitConverter.ToString(hash.Hash).Replace("-", "").ToLowerInvariant();
    }
}

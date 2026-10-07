using System.IO;
using System.Text;

namespace ImageTagPromptHelper;

/// <summary>danbooru 词表校验：让"中文 → 提示词"只输出词表里真实存在的 tag，而不是模型自己编。
/// 词表来源按顺序找：① 项目词表 app/booru_zh/*.csv（含中文名与别名，最全）；② SwarmUI 的补全词表
/// Data/Autocompletions/danbooru_zh.csv（只有 tag/分类/热度）。
/// 2026-10-07 为本机「图片标签工坊」项目加的本地功能。</summary>
public static class BooruDictionary
{
    /// <summary>项目词表目录（含中文名/别名）。</summary>
    public static string ProjectDictDir = @"E:\文档\ChatGPT\图片标签分类\app\booru_zh";

    /// <summary>备用词表（只有英文 tag）。</summary>
    public static string FallbackCsv = @"E:\SwarmUI\Data\Autocompletions\danbooru_zh.csv";

    /// <summary>规范化后的 tag → 规范写法（下划线形式）。</summary>
    public static Dictionary<string, string> ByName = new();

    /// <summary>规范化后的别名/中文名 → 规范写法。</summary>
    public static Dictionary<string, string> ByAlias = new();

    /// <summary>热度（用于挑最可能的那个）。</summary>
    public static Dictionary<string, long> Counts = new();

    /// <summary>不属于 danbooru 词表、但画画时该保留的质量/技术词。</summary>
    public static readonly HashSet<string> AllowedNonBooru = new(StringComparer.OrdinalIgnoreCase)
    {
        "masterpiece", "best quality", "very aesthetic", "absurdres", "highres", "high quality",
        "newest", "year 2024", "year 2025", "official art", "detailed background", "depth of field"
    };

    private static readonly object LoadLock = new();
    private static bool _loaded;

    public static bool Loaded => _loaded;

    public static void EnsureLoaded()
    {
        if (_loaded)
        {
            return;
        }
        lock (LoadLock)
        {
            if (_loaded)
            {
                return;
            }
            try
            {
                foreach (string name in new[] { "character", "copyright", "general", "meta" })
                {
                    string path = Path.Combine(ProjectDictDir, $"{name}.csv");
                    if (File.Exists(path))
                    {
                        LoadProjectCsv(path);
                    }
                }
                if (ByName.Count == 0 && File.Exists(FallbackCsv))
                {
                    LoadSimpleCsv(FallbackCsv);
                }
            }
            catch (Exception ex)
            {
                SwarmUI.Utils.Logs.Error($"BooruDictionary 载入失败：{ex.Message}");
            }
            _loaded = true;
            SwarmUI.Utils.Logs.Info($"BooruDictionary 载入完成：{ByName.Count} 个 tag、{ByAlias.Count} 个别名");
        }
    }

    /// <summary>把 tag 规范化成查表键：小写、去权重括号、下划线/空格统一、去转义。</summary>
    public static string Normalize(string raw)
    {
        if (string.IsNullOrWhiteSpace(raw))
        {
            return "";
        }
        string s = raw.Trim();
        // (tag:1.2) / (tag) 这类权重与括号
        int colon = s.LastIndexOf(':');
        if (s.StartsWith('(') && s.EndsWith(')') && colon > 0 && double.TryParse(s[(colon + 1)..^1], out _))
        {
            s = s[1..colon];
        }
        s = s.Replace("\\(", "(").Replace("\\)", ")").Replace("\\", "");
        s = s.Replace('_', ' ').Replace('\u3000', ' ').Trim();
        s = string.Join(' ', s.Split(' ', StringSplitOptions.RemoveEmptyEntries));
        return s.ToLowerInvariant();
    }

    /// <summary>把一个 tag 对到词表里的规范写法；对不上返回 null。</summary>
    public static string Resolve(string raw)
    {
        EnsureLoaded();
        string key = Normalize(raw).Replace(' ', '_');
        if (key.Length == 0)
        {
            return null;
        }
        if (ByName.TryGetValue(key, out string exact))
        {
            return exact;
        }
        if (ByAlias.TryGetValue(key, out string viaAlias))
        {
            return viaAlias;
        }
        if (AllowedNonBooru.Contains(Normalize(raw)))
        {
            return Normalize(raw);
        }
        // 单复数互试
        string alt = key.EndsWith('s') ? key[..^1] : key + "s";
        if (ByName.TryGetValue(alt, out string plural))
        {
            return plural;
        }
        if (ByAlias.TryGetValue(alt, out string pluralAlias))
        {
            return pluralAlias;
        }
        return null;
    }

    /// <summary>把模型给出的一整串提示词逐条校验，只保留词表里有的；返回（干净的串, 被丢掉的列表）。</summary>
    public static (string Clean, List<string> Dropped) Validate(string tagString)
    {
        List<string> kept = [];
        List<string> dropped = [];
        HashSet<string> seen = new(StringComparer.OrdinalIgnoreCase);
        foreach (string raw in tagString.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
        {
            string resolved = Resolve(raw);
            if (resolved is null)
            {
                dropped.Add(raw.Trim());
                continue;
            }
            string display = resolved.Replace('_', ' ');
            if (seen.Add(display))
            {
                kept.Add(display);
            }
        }
        return (string.Join(", ", kept), dropped);
    }

    private static void LoadProjectCsv(string path)
    {
        string[] lines = File.ReadAllLines(path, Encoding.UTF8);
        for (int i = 1; i < lines.Length; i++)
        {
            string[] parts = SplitCsv(lines[i]);
            if (parts.Length < 5)
            {
                continue;
            }
            string tag = parts[0].Trim();
            if (tag.Length == 0)
            {
                continue;
            }
            string key = tag.ToLowerInvariant().Replace(' ', '_');
            ByName[key] = key;
            long.TryParse(parts[4].Trim(), out long count);
            Counts[key] = count;
            foreach (string alias in parts[2].Split('|', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
            {
                string aliasKey = alias.ToLowerInvariant().Replace(' ', '_');
                if (aliasKey.Length > 1 && (!ByAlias.TryGetValue(aliasKey, out string old) || GetCount(key) >= GetCount(old)))
                {
                    ByAlias[aliasKey] = key;
                }
            }
            string zh = parts[3].Trim();
            if (zh.Length > 1)
            {
                string zhKey = zh.ToLowerInvariant().Replace(' ', '_');
                if (!ByAlias.TryGetValue(zhKey, out string oldZh) || GetCount(key) >= GetCount(oldZh))
                {
                    ByAlias[zhKey] = key;
                }
            }
        }
    }

    private static void LoadSimpleCsv(string path)
    {
        foreach (string line in File.ReadAllLines(path, Encoding.UTF8))
        {
            string[] parts = line.Split(',');
            if (parts.Length == 0)
            {
                continue;
            }
            string tag = parts[0].Trim();
            if (tag.Length == 0)
            {
                continue;
            }
            string key = tag.ToLowerInvariant().Replace(' ', '_');
            ByName[key] = key;
            Counts[key] = parts.Length > 2 && long.TryParse(parts[2].Trim(), out long c) ? c : 0;
        }
    }

    private static long GetCount(string key) => Counts.TryGetValue(key, out long c) ? c : 0;

    /// <summary>极简 CSV 切分（我们的词表里引号字段很少，够用）。</summary>
    private static string[] SplitCsv(string line)
    {
        List<string> parts = [];
        StringBuilder cur = new();
        bool inQuotes = false;
        foreach (char c in line)
        {
            if (c == '"')
            {
                inQuotes = !inQuotes;
            }
            else if (c == ',' && !inQuotes)
            {
                parts.Add(cur.ToString());
                cur.Clear();
            }
            else
            {
                cur.Append(c);
            }
        }
        parts.Add(cur.ToString());
        return [.. parts];
    }
}

using Newtonsoft.Json.Linq;
using SwarmUI.Accounts;
using SwarmUI.Core;
using SwarmUI.Utils;
using SwarmUI.WebAPI;

// 注意：命名空间**不能**以 "SwarmUI." 开头，否则 SwarmUI 会把它当成内置扩展去找 src/BuiltinExtensions。
namespace ImageTagPromptHelper;

/// <summary>中文自然语言 → 提示词 的小助手（本地 Ollama，离线）。
/// 2026-10-07 为本机「图片标签工坊」项目加的本地扩展：界面里写中文描述，点一下变成 danbooru 风格提示词。</summary>
public class PromptHelperExtension : Extension
{
    /// <summary>本机 Ollama 地址。</summary>
    public static string OllamaHost = "http://127.0.0.1:11434";

    /// <summary>用的模型（本机最好的可用模型）。</summary>
    public static string OllamaModel = "qwen3-8b:latest";

    public static string PromptSystem =
        "你是 Stable Diffusion / 动漫绘图提示词工程师。把用户的中文描述改写成 danbooru 风格的英文 tag 串（用英文逗号+空格分隔）。"
        + "要求：1) 只输出 tag 串本身，不要解释、不要引号、不要编号；"
        + "2) 按重要性排序：主体数量→角色→发型发色→服装→动作姿势→视线表情→镜头与构图→背景→光线氛围→画质词；"
        + "3) 用 danbooru 常用写法（1girl/2girls、aqua twintails、thighhighs、from side、upper body、medium shot、"
        + "detailed background、soft lighting 之类）；角色若为知名角色直接用角色 tag（hatsune miku / kasane teto）；"
        + "4) 用户没说的不要乱加，但可补少量合理画质词：masterpiece, best quality, very aesthetic, absurdres；"
        + "5) 输出控制在 60 个 tag 以内；"
        + "6) **优先用 danbooru 上真实存在的常见 tag**（宁可换个更常见的说法，也别拼短语/造词）；"
        + "7) 光线下写 lighting 类 tag（soft lighting / cinematic lighting），视角写 from side / from above，"
        + "表情写 smiling / open mouth，视线写 looking at viewer / looking away，别写成句子；"
        + "8) 绝对不要输出 bad tag、none、N/A 这类占位词；"
        + "9) 实在想不到对应 tag，再用最接近的简短英文描述。";

    public static string NegativeSystem =
        "你是 Stable Diffusion 负向提示词助手。根据用户要画的内容，给出简短的英文负向 tag 串（英文逗号+空格分隔，20 个以内），"
        + "只输出 tag 串本身。固定包含：lowres, worst quality, low quality, bad anatomy, bad hands, extra digits, "
        + "fewer digits, extra limbs, deformed, watermark, signature, username, artist name, text, logo, jpeg artifacts, cropped, out of frame。"
        + "若描述里提到双人/多人，再加：3girls, 4girls, multiple girls, extra girls, extra person, clone, duplicated。";

    public static string OutfitSystem =
        "你是服装设计提示词助手。把用户的中文服装描述转成 danbooru 风格的英文 tag 串（英文逗号+空格分隔），"
        + "只输出 tag 串本身：先写整体款式（dress / jacket / uniform / leotard 等），再写细节（袖子、领口、长度、花纹、材质、颜色），"
        + "最后写鞋子/袜子/配饰；不要加人物与背景 tag。";

    public override void OnPreInit()
    {
        ScriptFiles.Add("Assets/prompt_helper.js");
        StyleSheetFiles.Add("Assets/prompt_helper.css");
    }

    public override void OnInit()
    {
        // 路由名 = 方法名，所以方法就叫 PromptHelper → POST /API/PromptHelper
        API.RegisterAPICall(PromptHelper, true, Permissions.BasicImageGeneration);
        Logs.Info($"PromptHelper ready (Ollama {OllamaHost}, model {OllamaModel})");
    }

    public async Task<JObject> PromptHelper(Session session, string text, string mode = "prompt")
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            return new JObject() { ["error"] = "请输入中文描述。" };
        }
        string system = mode switch
        {
            "negative" => NegativeSystem,
            "outfit" => OutfitSystem,
            _ => PromptSystem
        };
        JObject body = new()
        {
            ["model"] = OllamaModel,
            ["stream"] = false,
            ["think"] = false,
            ["options"] = new JObject() { ["temperature"] = 0.4 },
            ["messages"] = new JArray()
            {
                new JObject() { ["role"] = "system", ["content"] = system },
                new JObject() { ["role"] = "user", ["content"] = text }
            }
        };
        try
        {
            JObject result = await Utilities.UtilWebClient.PostJson($"{OllamaHost}/api/chat", body);
            string content = result["message"]?["content"]?.ToString() ?? "";
            content = content.Trim().Trim('`').Trim();
            if (content.StartsWith("json"))
            {
                content = content["json".Length..].Trim();
            }
            if (string.IsNullOrWhiteSpace(content))
            {
                return new JObject() { ["error"] = "本地模型没有返回内容（Ollama 是否在运行？）" };
            }
            // 用 danbooru 词表校验：能对上的一律换成词表规范写法；对不上的保留但标记出来
            (string clean, List<string> invented) = BooruDictionary.Validate(content);
            if (string.IsNullOrWhiteSpace(clean))
            {
                return new JObject()
                {
                    ["error"] = "没能生成有效 tag，请把描述写得更具体一点。"
                };
            }
            return new JObject()
            {
                ["result"] = clean,
                ["invented"] = invented.Count == 0 ? "" : string.Join(", ", invented)
            };
        }
        catch (Exception ex)
        {
            Logs.Error($"PromptHelper 调用 Ollama 失败：{ex.ReadableString()}");
            return new JObject() { ["error"] = $"调用本机 Ollama 失败：{ex.Message}" };
        }
    }
}

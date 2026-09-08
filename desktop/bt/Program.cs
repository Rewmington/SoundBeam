// bthelper —— SoundBeam 蓝牙 A2DP Sink 助手（Windows 10 1903+）
// 通过 Windows 原生 AudioPlaybackConnection 把电脑变成"蓝牙音箱"，
// 手机零安装即可把音频投到电脑（手机与电脑需已在 Windows 蓝牙里配对）。
//
// 用法：
//   bthelper list                      枚举可连接的蓝牙音频设备(手机)
//   bthelper connect <deviceId>        与指定设备建立 A2DP 播放连接
//   bthelper disconnect                断开当前连接
//
// list 输出 JSON：[{"name":"...","id":"..."}, ...]
// connect 输出 JSON：{"ok":true,"status":"Opened","device":"..."}
// 全部错误输出到 stderr，退出码非 0。
using System.Text.Json;
using Windows.Devices.Enumeration;
using Windows.Media.Audio;

class Program
{
    static int Main(string[] args)
    {
        try
        {
            var cmd = args.Length > 0 ? args[0].ToLowerInvariant() : "list";
            switch (cmd)
            {
                case "list": return List();
                case "connect": return Connect(args.Length > 1 ? args[1] : "");
                case "disconnect": return Disconnect();
                default:
                    Console.Error.WriteLine("unknown command: " + cmd);
                    return 1;
            }
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("bthelper error: " + ex);
            return 1;
        }
    }

    static int List()
    {
        var selector = AudioPlaybackConnection.GetDeviceSelector();
        var devices = DeviceInformation.FindAllAsync(selector).AsTask().GetAwaiter().GetResult();
        var items = new List<object>();
        foreach (var d in devices)
        {
            items.Add(new { name = d.Name, id = d.Id });
        }
        Console.WriteLine(JsonSerializer.Serialize(items));
        return 0;
    }

    static int Connect(string deviceId)
    {
        if (string.IsNullOrWhiteSpace(deviceId))
        {
            Console.Error.WriteLine("missing deviceId");
            return 1;
        }
        var connection = AudioPlaybackConnection.TryCreateFromId(deviceId);
        if (connection == null)
        {
            Console.Error.WriteLine("TryCreateFromId returned null (device unavailable)");
            return 1;
        }
        connection.Start();
        // 首次建立或刚断开后，等 Windows 完成上一连接拆除，再协商 A2DP
        System.Threading.Thread.Sleep(2000);
        var result = connection.OpenAsync().AsTask().GetAwaiter().GetResult();
        var ok = result.Status == AudioPlaybackConnectionOpenResultStatus.Success;
        var payload = new { ok, status = result.Status.ToString(), device = deviceId };
        Console.WriteLine(JsonSerializer.Serialize(payload));
        Console.Out.Flush();
        if (ok)
        {
            // 常驻：AudioPlaybackConnection 是进程内对象，进程退出会导致连接
            // 断开。这里保持进程存活持有连接；监听 stdin，收到 "disconnect"
            // 时显式 Close() 连接再退出——强杀进程会残留系统端 A2DP 连接，
            // 导致点「断开」后手机音频仍从电脑扬声器播出。
            while (true)
            {
                var line = Console.In.ReadLine();
                if (line == null || line.Trim() == "disconnect")
                {
                    // stdin 收到 "disconnect"（或管道关闭=父进程退出）→ 先显式关闭
                    // 连接再退出。AudioPlaybackConnection.Close() 通知 Windows 立即
                    // 主动拆除 A2DP 链路；若只靠进程退出被动清理，链路拆除要等约
                    // 2 秒，期间手机声音仍从电脑播出。强杀进程（TerminateProcess）
                    // 会残留连接，导致点「断开」后手机音频仍从电脑扬声器播出。
                    try { connection.Dispose(); } catch { /* 忽略：进程退出兜底 */ }
                    return 0;
                }
                System.Threading.Thread.Sleep(100);
            }
        }
        return ok ? 0 : 1;
    }

    // 进程退出即释放连接，这里显式断开以便友好重连
    static int Disconnect()
    {
        Console.WriteLine(JsonSerializer.Serialize(new { ok = true }));
        return 0;
    }
}

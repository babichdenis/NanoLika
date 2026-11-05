// A read-only illustration panel, hosted inside NSIS's existing progress page.
// Its own UI thread never executes installer commands, reads accounts or uses the network.
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Text;
using System.IO;
using System.Globalization;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;

public sealed class TipText { public string title; public string body; }
public sealed class Tip { public string image; public TipText en; public TipText zh; public TipText ru; }
public sealed class ControlsText { public string pause; public string resume; public string pauseLabel; public string resumeLabel; }
public sealed class Catalog { public int intervalMs; public Tip[] slides; public Dictionary<string, ControlsText> controls; }

internal static class Native {
    [StructLayout(LayoutKind.Sequential)] internal struct Rect { public int left, top, right, bottom; }
    [DllImport("user32.dll")] internal static extern bool IsWindow(IntPtr h);
    [DllImport("user32.dll")] internal static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] internal static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] internal static extern IntPtr GetAncestor(IntPtr h, uint flag);
    [DllImport("user32.dll")] internal static extern IntPtr GetDlgItem(IntPtr h, int id);
    [DllImport("user32.dll")] internal static extern bool GetClientRect(IntPtr h, out Rect r);
    [DllImport("user32.dll")] internal static extern bool MoveWindow(IntPtr h, int x, int y, int w, int height, bool paint);
    [DllImport("user32.dll")] internal static extern IntPtr SetParent(IntPtr child, IntPtr parent);
    [DllImport("user32.dll")] internal static extern int GetWindowLong(IntPtr h, int index);
    [DllImport("user32.dll")] internal static extern int SetWindowLong(IntPtr h, int index, int value);
    [DllImport("user32.dll")] internal static extern bool SystemParametersInfo(uint action, uint param, out bool value, uint flags);
}

internal sealed class Artwork : IDisposable {
    internal readonly Catalog Data;
    private readonly Dictionary<string, Image> images = new Dictionary<string, Image>();
    internal Artwork() {
        var assembly = Assembly.GetExecutingAssembly();
        using (var reader = new StreamReader(assembly.GetManifestResourceStream("NanoLika.Slides"), Encoding.UTF8))
            Data = new JavaScriptSerializer().Deserialize<Catalog>(reader.ReadToEnd());
        foreach (var name in new [] { "working", "idle", "waiting", "happy" })
            using (var stream = assembly.GetManifestResourceStream("NanoLika." + name))
            using (var original = Image.FromStream(stream)) images.Add(name, new Bitmap(original));
    }
    internal Image Get(string name) { return images[name]; }
    public void Dispose() { foreach (var image in images.Values) image.Dispose(); }
}

internal static class TipDrawing {
    internal static readonly Color Paper = Color.FromArgb(247, 241, 230);
    internal static readonly Color Ink = Color.FromArgb(43, 38, 34);
    internal static readonly Color Green = Color.FromArgb(65, 94, 60);
    internal static TipText Text(Tip tip, string locale) { return locale == "ru" ? tip.ru : locale == "zh" ? tip.zh : tip.en; }
    internal static Rectangle PauseBounds(Size size, float dpi) { return new Rectangle(size.Width - (int)(112 * dpi), size.Height - (int)(27 * dpi), (int)(108 * dpi), (int)(24 * dpi)); }

    // The installer and the offscreen render check call the same renderer.
    internal static bool Draw(Graphics g, Size size, Artwork art, int index, string locale, bool paused, float dpi, bool highContrast) {
        g.Clear(highContrast ? SystemColors.Window : Paper);
        g.SmoothingMode = SmoothingMode.HighQuality;
        g.InterpolationMode = InterpolationMode.HighQualityBicubic;
        g.TextRenderingHint = TextRenderingHint.ClearTypeGridFit;
        float pad = 8 * dpi, gap = 18 * dpi;
        float side = Math.Min(size.Height, size.Width * 0.35f);
        var tip = art.Data.slides[index];
        g.DrawImage(art.Get(tip.image), new RectangleF(0, (size.Height-side)/2, side, side));
        float x = side + gap, width = size.Width - x - pad;
        var text = Text(tip, locale);
        var button = PauseBounds(size, dpi);
        using (var titleFont = new Font("Segoe UI", 20 * dpi, FontStyle.Bold, GraphicsUnit.Pixel))
        using (var format = new StringFormat(StringFormatFlags.LineLimit))
        using (var titleBrush = new SolidBrush(highContrast ? SystemColors.WindowText : Green))
        using (var bodyBrush = new SolidBrush(highContrast ? SystemColors.WindowText : Ink)) {
            var titleSize = g.MeasureString(text.title, titleFont, (int)width, format);
            g.DrawString(text.title, titleFont, titleBrush, new RectangleF(x, pad, width, titleSize.Height + dpi), format);
            float y = pad + titleSize.Height + 8 * dpi;
            float available = button.Top - y - 7 * dpi;
            for (float fontSize = 14.5f; fontSize >= 12; fontSize -= 0.5f) {
                using (var font = new Font("Segoe UI", fontSize * dpi, FontStyle.Regular, GraphicsUnit.Pixel)) {
                    var measured = g.MeasureString(text.body, font, (int)width, format);
                    if (measured.Height <= available) {
                        g.DrawString(text.body, font, bodyBrush, new RectangleF(x, y, width, available), format);
                        return true;
                    }
                }
            }
        }
        return false;
    }
}

internal sealed class TipCanvas : Control {
    internal readonly Artwork Art;
    internal readonly string Locale;
    internal int Index;
    internal bool Paused;
    internal float Dpi = 1;
    internal TipCanvas(Artwork art, string locale) {
        Art = art; Locale = locale;
        DoubleBuffered = true; BackColor = TipDrawing.Paper;
        AccessibleRole = AccessibleRole.StaticText;
        UpdateDescription();
    }
    internal void UpdateDescription() {
        var text = TipDrawing.Text(Art.Data.slides[Index], Locale);
        AccessibleName = text.title; AccessibleDescription = text.body;
        Invalidate();
    }
    protected override void OnPaint(PaintEventArgs args) { TipDrawing.Draw(args.Graphics, ClientSize, Art, Index, Locale, Paused, Dpi, SystemInformation.HighContrast); }
}

internal sealed class TipsWindow : Form {
    private readonly IntPtr parent;
    private readonly IntPtr details;
    private readonly IntPtr root;
    private readonly TipCanvas canvas;
    private readonly Button pause;
    private readonly Timer poll = new Timer();
    private DateTime lastSlide = DateTime.UtcNow;
    private readonly Artwork art;
    private readonly string locale;
    private Size lastSize;
    private Font pauseFont;

    internal TipsWindow(IntPtr parentWindow, string language, Artwork artwork) {
        parent = parentWindow; locale = language; art = artwork;
        root = Native.GetAncestor(parent, 2); details = Native.GetDlgItem(parent, 1016);
        Text = "NanoLika.SetupTips";
        SetBounds(-32000, -32000, 1, 1);
        FormBorderStyle = FormBorderStyle.None; ShowInTaskbar = false;
        StartPosition = FormStartPosition.Manual; AutoScaleMode = AutoScaleMode.None;
        canvas = new TipCanvas(art, locale) { Dock = DockStyle.Fill };
        bool animations;
        canvas.Paused = Native.SystemParametersInfo(0x1042, 0, out animations, 0) && !animations;
        Controls.Add(canvas);
        pause = new Button { FlatStyle = FlatStyle.Flat, BackColor = TipDrawing.Paper, ForeColor = TipDrawing.Green, TabStop = true };
        pause.FlatAppearance.BorderSize = 0;
        pause.Click += delegate { canvas.Paused = !canvas.Paused; lastSlide = DateTime.UtcNow; UpdatePause(); };
        canvas.Controls.Add(pause); UpdatePause();
        poll.Interval = 250;
        poll.Tick += Tick;
    }
    protected override bool ShowWithoutActivation { get { return true; } }
    protected override void OnShown(EventArgs args) {
        base.OnShown(args);
        // Keep the real NSIS progress bar, status, details and error dialogs in place.
        Native.SetWindowLong(Handle, -16, (Native.GetWindowLong(Handle, -16) & ~unchecked((int)0x80000000)) | 0x40000000);
        Native.SetParent(Handle, parent);
        LayoutHost(); poll.Start();
    }
    private void UpdatePause() {
        var words = art.Data.controls[locale];
        pause.Text = canvas.Paused ? words.resume : words.pause;
        pause.AccessibleName = canvas.Paused ? words.resumeLabel : words.pauseLabel;
    }
    private void LayoutHost() {
        Native.Rect r;
        if (!Native.GetClientRect(parent, out r)) return;
        var size = new Size(r.right, r.bottom);
        if (size == lastSize) return;
        lastSize = size;
        using (var graphics = Graphics.FromHwnd(parent)) canvas.Dpi = graphics.DpiX / 96f;
        bool detailsButtonVisible = Native.IsWindowVisible(Native.GetDlgItem(parent, 1027));
        int footer = (int)((detailsButtonVisible ? 68 : 44) * canvas.Dpi);
        if (size.Width < 350 * canvas.Dpi || size.Height-footer < 152*canvas.Dpi) { Close(); return; }
        Native.MoveWindow(Handle, 0, 0, size.Width, size.Height - footer, true);
        // NSIS resource IDs: progress 1004, current status 1006, details 1016/1027.
        Native.MoveWindow(Native.GetDlgItem(parent, 1006), 0, size.Height-footer+6, size.Width, (int)(18*canvas.Dpi), true);
        Native.MoveWindow(Native.GetDlgItem(parent, 1004), 0, size.Height-(int)((detailsButtonVisible ? 40 : 16)*canvas.Dpi), size.Width, (int)(12*canvas.Dpi), true);
        Native.MoveWindow(Native.GetDlgItem(parent, 1027), 0, size.Height-(int)(24*canvas.Dpi), (int)(110*canvas.Dpi), (int)(22*canvas.Dpi), true);
        Native.MoveWindow(details, 0, 0, size.Width, size.Height-footer, true);
        pause.Bounds = TipDrawing.PauseBounds(canvas.ClientSize, canvas.Dpi);
        var oldFont = pauseFont;
        pauseFont = new Font("Segoe UI", 12 * canvas.Dpi, GraphicsUnit.Pixel);
        pause.Font = pauseFont;
        if (oldFont != null) oldFont.Dispose();
        canvas.UpdateDescription();
    }
    private void Tick(object sender, EventArgs args) {
        if (!Native.IsWindow(parent) || !Native.IsWindowVisible(parent)) { Close(); return; }
        bool showingDetails = Native.IsWindowVisible(details);
        if (Visible == showingDetails) Visible = !showingDetails;
        if (showingDetails || Native.IsIconic(root)) { lastSlide = DateTime.UtcNow; return; }
        LayoutHost();
        if (!canvas.Paused && (DateTime.UtcNow-lastSlide).TotalMilliseconds >= art.Data.intervalMs) {
            canvas.Index = (canvas.Index+1) % art.Data.slides.Length;
            canvas.UpdateDescription(); lastSlide = DateTime.UtcNow;
        }
    }
    protected override void Dispose(bool disposing) {
        if (disposing) { poll.Stop(); poll.Dispose(); if (pauseFont != null) pauseFont.Dispose(); }
        base.Dispose(disposing);
    }
}

internal static class Program {
    [STAThread] private static int Main(string[] args) {
        try {
            using (var art = new Artwork()) {
                if (args.Length == 2 && args[0] == "--render-check") return Render(art, args[1]);
                long number;
                if (args.Length != 2) return 0;
                bool parsed = args[0].StartsWith("0x", StringComparison.OrdinalIgnoreCase)
                    ? long.TryParse(args[0].Substring(2), NumberStyles.HexNumber, CultureInfo.InvariantCulture, out number)
                    : long.TryParse(args[0], NumberStyles.Integer, CultureInfo.InvariantCulture, out number);
                if (!parsed || !Native.IsWindow(new IntPtr(number))) return 0;
                var parent = new IntPtr(number);
                if (Native.GetDlgItem(parent, 1004) == IntPtr.Zero || Native.GetDlgItem(parent, 1016) == IntPtr.Zero) return 0;
                Application.SetUnhandledExceptionMode(UnhandledExceptionMode.ThrowException);
                Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
                string locale = args[1] == "1049" ? "ru" : args[1] == "2052" ? "zh" : "en";
                using (var window = new TipsWindow(parent, locale, art)) Application.Run(window);
            }
            return 0;
        } catch { return 1; } // An optional picture panel cannot interrupt the installer.
    }
    private static int Render(Artwork art, string directory) {
        Directory.CreateDirectory(directory);
        foreach (var locale in new [] { "en", "zh", "ru" })
        foreach (int dpi in new [] {96,144,192})
        foreach (int width in new [] {450,500})
        for (int index=0; index<art.Data.slides.Length; index++) {
            float scale = dpi/96f;
            // Default MUI content bounds, including the space reserved for real progress.
            using (var bitmap = new Bitmap((int)(width*scale), (int)(152*scale)))
            using (var graphics = Graphics.FromImage(bitmap)) {
                if (!TipDrawing.Draw(graphics, bitmap.Size, art, index, locale, false, scale, false)) {
                    File.WriteAllText(Path.Combine(directory, "failure.json"), new JavaScriptSerializer().Serialize(new { locale = locale, dpi = dpi, width = width, slide = index }));
                    return 2;
                }
                bitmap.Save(Path.Combine(directory, locale+"-"+dpi+"-"+width+"-"+index+".png"));
            }
        }
        File.WriteAllText(Path.Combine(directory,"result.json"), "{\"locales\":3,\"scales\":3,\"widths\":2,\"slides\":"+art.Data.slides.Length+",\"passed\":true}");
        return 0;
    }
}

// The run's recording on its Result page: a well 230 px tall (200 in a narrow window, the video letterboxed in it),
// then play and pause, a scrub line, the time and the length, and show in folder on one row (the file selected in File
// Explorer, Finder or the file manager; an icon button, the folder Run folder has too). It plays with a VideoPlayer on a hidden object (HideAndDontSave) rendering into a RenderTexture shown in the box, made only when
// it is first played or scrubbed: nothing plays by itself, and a page that only shows the box costs nothing. While it
// plays it is repainted from EditorApplication.update (in edit mode too); it is let go when the page goes (another run,
// the window closed, a script reload). A project without the Video module plays the recording's frames (the JPEGs the
// agent service keeps beside the MP4) as a flipbook in the same box, as Godot's dock does (Flipbook below); with no
// frames either, Play opens the MP4 in the computer's own player.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;
#if AUTOPILOT_VIDEO
using UnityEngine.Video;
#endif
using static Autopilot.Playtest.Editor.AutopilotUI;

namespace Autopilot.Playtest.Editor
{
    public sealed class RecordingPlayer : VisualElement
    {
        /// <summary>The players holding a VideoPlayer now (let go before a script reload: their objects would outlive it).</summary>
        static readonly HashSet<RecordingPlayer> holding = new HashSet<RecordingPlayer>();

        [InitializeOnLoadMethod]
        static void LetGoBeforeReload()
        {
            AssemblyReloadEvents.beforeAssemblyReload += ReleaseAll;
        }

        public static void ReleaseAll()
        {
            foreach (var p in new List<RecordingPlayer>(holding)) p.Release();
        }

        public readonly string VideoPath;
        double length;
        readonly VisualElement frame;
        readonly Image image;
        readonly PlayMark poster;
        readonly Glyph playGlyph;
        readonly VisualElement playButton;
        VisualElement showButton;
        readonly Label time, problem;
        readonly Scrub scrub;
        bool playing, ended, hooked;
        double settleUntil;  // (paused: repainted until then, for a frame scrubbed to)
        double pendingSeek = -1;
#if AUTOPILOT_VIDEO
        GameObject host;
        VideoPlayer player;
        RenderTexture texture;
#endif

        /// <summary>Whether a recording plays here from its frames alone (no Video module; the frames read as JPEGs).</summary>
        public static bool PlaysFrames
        {
            get
            {
#if !AUTOPILOT_VIDEO && AUTOPILOT_IMAGECONVERSION
                return true;
#else
                return false;
#endif
            }
        }

        readonly string framesDir;
        readonly double fps;

        public RecordingPlayer(string path, double seconds) : this(path, seconds, "", 0)
        {
        }

        public RecordingPlayer(string path, double seconds, string frames, double framesFps)
        {
            VideoPath = path ?? "";
            framesDir = frames ?? "";
            fps = framesFps > 0 ? framesFps : 2;
            length = Math.Max(0, seconds);
            AddToClassList("recording");

            // The box: a well as wide as the page, 230 px tall (200 in a narrow window), the video letterboxed in it
            frame = Box("rec-frame");
            image = new Image { scaleMode = ScaleMode.ScaleToFit };
            image.AddToClassList("rec-image");
            frame.Add(image);
            poster = new PlayMark();
            frame.Add(poster);
            frame.AddManipulator(new Clickable(Toggle));
            Add(frame);

            // Play, the scrub line, the time and show in folder: one row
            var controls = Row("rec-controls");
            playButton = IconBtn(Glyph.Shape.Play, PlayName, Toggle);
            playGlyph = playButton.Q<Glyph>();
            controls.Add(playButton);
            scrub = new Scrub((float)Math.Max(0.1, length));
            scrub.Changed += Seek;
            controls.Add(scrub);
            time = L(Times(0), "rec-time");
            controls.Add(time);
            showButton = IconBtn(Glyph.Shape.Folder, ShowName, ShowInFolder, "quiet");
            controls.Add(showButton);
            Add(controls);
            problem = L("", "rec-problem");
            problem.style.display = DisplayStyle.None;
            Add(problem);

            RegisterCallback<DetachFromPanelEvent>(_ => Release());
            // Gone from its place: the player kept, Play and Show in File Explorer disabled, saying so
            if (!File.Exists(VideoPath) && !(PlaysFrames && Directory.Exists(framesDir))) Gone();
        }

        public const string PlayName = "Play";
        public const string PauseName = "Pause";
        /// <summary>The show-in-folder button's name: what the computer calls its file manager.</summary>
        public static string ShowName => Application.platform == RuntimePlatform.WindowsEditor ? "Show in File Explorer"
            : Application.platform == RuntimePlatform.OSXEditor ? "Show in Finder" : "Show in folder";

        // (the position m:ss, the length as durations are, mm:ss: "1:18 / 03:26")
        string Times(double at) => $"{Clock(at)} / {AutopilotText.Clock(length)}";

        static string Clock(double seconds)
        {
            var s = (long)Math.Max(0, Math.Floor(double.IsNaN(seconds) ? 0 : seconds));
            return s >= 3600
                ? string.Format(CultureInfo.InvariantCulture, "{0}:{1:00}:{2:00}", s / 3600, s / 60 % 60, s % 60)
                : string.Format(CultureInfo.InvariantCulture, "{0}:{1:00}", s / 60, s % 60);
        }

        void ShowInFolder()
        {
            // (File Explorer and Finder select the file; on Linux the file manager opens its folder; frames alone: their folder)
            if (File.Exists(VideoPath)) EditorUtility.RevealInFinder(VideoPath);
            else if (Directory.Exists(framesDir)) EditorUtility.RevealInFinder(framesDir);
        }

        public const string GoneLine = "The recording isn’t there any more.";

        void Gone()
        {
            playButton.SetEnabled(false);
            showButton.SetEnabled(false);
            Say(GoneLine);
            poster.Warning = true;
        }

        /// <summary>It can’t play here: Play disabled, the warning mark in the poster's place.</summary>
        void CantPlay()
        {
            playButton.SetEnabled(false);
            poster.style.display = DisplayStyle.Flex;
            poster.Warning = true;
        }

        void Say(string text)
        {
            problem.text = text;
            problem.style.display = string.IsNullOrEmpty(text) ? DisplayStyle.None : DisplayStyle.Flex;
        }

        void ShowPlaying(bool on)
        {
            playing = on;
            playGlyph.Is = on ? Glyph.Shape.Pause : Glyph.Shape.Play;
            playButton.tooltip = playButton.name = on ? PauseName : PlayName;
            poster.style.display = on || HasPicture ? DisplayStyle.None : DisplayStyle.Flex;
            if (on) Hook();
        }

#if AUTOPILOT_VIDEO
        bool HasPicture => texture != null;

        /// <summary>The VideoPlayer, made the first time it is needed (false: the file is gone).</summary>
        bool Make()
        {
            if (player != null) return true;
            if (!File.Exists(VideoPath))
            {
                Gone();
                return false;
            }
            host = new GameObject("Autopilot recording") { hideFlags = HideFlags.HideAndDontSave };
            player = host.AddComponent<VideoPlayer>();
            player.playOnAwake = false;
            player.isLooping = false;
            player.skipOnDrop = true;
            player.source = VideoSource.Url;
            player.url = VideoPath;
            player.renderMode = VideoRenderMode.RenderTexture;
            player.audioOutputMode = VideoAudioOutputMode.None;
            player.prepareCompleted += Prepared;
            player.loopPointReached += _ =>
            {
                ended = true;
                ShowPlaying(false);
            };
            player.errorReceived += (_, message) =>
            {
                ShowPlaying(false);
                Say($"This recording can’t play here: {ShowName} (the button beside the time) and open it from there.");
                CantPlay();
                Debug.LogWarning("[Autopilot] The recording couldn't play: " + message);
            };
            holding.Add(this);
            player.Prepare();
            return true;
        }

        void Prepared(VideoPlayer p)
        {
            if (p != player) return;
            int w = (int)p.width, h = (int)p.height;
            if (w <= 0 || h <= 0)
            {
                w = 1280;
                h = 720;
            }
            if (texture == null || texture.width != w || texture.height != h)
            {
                if (texture != null)
                {
                    texture.Release();
                    UnityEngine.Object.DestroyImmediate(texture);
                }
                texture = new RenderTexture(w, h, 0) { hideFlags = HideFlags.HideAndDontSave, name = "Autopilot recording" };
                texture.Create();
            }
            p.targetTexture = texture;
            image.image = texture;
            if (p.length > 0)
            {
                length = p.length;
                scrub.highValue = (float)length;
            }
            if (pendingSeek >= 0 && p.canSetTime) p.time = pendingSeek;
            pendingSeek = -1;
            if (!playing) p.Pause();  // (shows the frame scrubbed to)
            poster.style.display = DisplayStyle.None;
            Settle();
        }

        void Toggle()
        {
            if (playing)
            {
                player?.Pause();
                ShowPlaying(false);
                return;
            }
            if (!Make()) return;
            Say("");
            if (ended)
            {
                ended = false;
                if (player.canSetTime) player.time = 0;
            }
            player.Play();
            ShowPlaying(true);
        }

        void Seek(float to)
        {
            ended = false;
            time.text = Times(to);
            if (!Make()) return;
            if (player.isPrepared && player.canSetTime)
            {
                player.time = to;
                Settle();  // (the frame scrubbed to shows once it is decoded)
            }
            else pendingSeek = to;
        }

        void Tick()
        {
            if (player == null) return;
            var at = player.time;
            scrub.SetValueWithoutNotify((float)at);
            time.text = Times(at);
            image.MarkDirtyRepaint();
            // (in edit mode the video advances with the player loop)
            if (playing || EditorApplication.timeSinceStartup < settleUntil) EditorApplication.QueuePlayerLoopUpdate();
            else Unhook();
        }

        void Settle()
        {
            settleUntil = EditorApplication.timeSinceStartup + 0.5;
            Hook();
        }

        /// <summary>The VideoPlayer and its texture let go (the page went, or a script reload comes).</summary>
        public void Release()
        {
            Unhook();
            holding.Remove(this);
            if (player != null)
            {
                player.Stop();
                player.targetTexture = null;
            }
            if (host != null) UnityEngine.Object.DestroyImmediate(host);
            if (texture != null)
            {
                texture.Release();
                UnityEngine.Object.DestroyImmediate(texture);
            }
            image.image = null;
            player = null;
            host = null;
            texture = null;
            if (playing) ShowPlaying(false);
        }
#else
        // (the project has no Video module: the frames as a flipbook when it can read JPEGs and they are there, else
        // Play opens the MP4 in the computer's own player)
        Flipbook flip;
        double at, lastTick;
        bool HasPicture => flip != null && flip.Shown >= 0;

        bool Flip()
        {
            if (flip != null) return true;
            if (!PlaysFrames) return false;
            flip = Flipbook.Open(framesDir, fps);
            if (flip == null) return false;
            holding.Add(this);
            if (length <= 0)
            {
                length = flip.Length;
                scrub.highValue = (float)length;
            }
            return true;
        }

        void Toggle()
        {
            if (playing)
            {
                ShowPlaying(false);
                return;
            }
            if (Flip())
            {
                if (ended || at >= length)
                {
                    ended = false;
                    at = 0;
                }
                lastTick = EditorApplication.timeSinceStartup;
                Say("");
                Picture();
                ShowPlaying(true);
                return;
            }
            if (File.Exists(VideoPath)) EditorUtility.OpenWithDefaultApp(VideoPath);
        }

        void Seek(float to)
        {
            ended = false;
            at = to;
            time.text = Times(to);
            if (Flip()) Picture();
        }

        void Picture()
        {
            var tex = flip?.Show(at);
            if (tex == null) return;
            image.image = tex;
            poster.style.display = DisplayStyle.None;
            image.MarkDirtyRepaint();
        }

        void Tick()
        {
            if (!playing || flip == null)
            {
                Unhook();
                return;
            }
            var now = EditorApplication.timeSinceStartup;
            at += now - lastTick;
            lastTick = now;
            if (at >= length)
            {
                at = length;
                ended = true;
                ShowPlaying(false);
            }
            scrub.SetValueWithoutNotify((float)at);
            time.text = Times(at);
            Picture();
        }

        public void Release()
        {
            Unhook();
            holding.Remove(this);
            flip?.Dispose();
            flip = null;
            image.image = null;
            if (playing) ShowPlaying(false);
        }
#endif

        void Hook()
        {
            if (hooked) return;
            hooked = true;
            EditorApplication.update += Tick;
        }

        void Unhook()
        {
            if (!hooked) return;
            hooked = false;
            EditorApplication.update -= Tick;
        }

        /// <summary>The recording's frames (JPEGs in a folder, in name order) shown at a time: the frame at t * fps,
        /// read into one texture when it changes. What a project without the Video module plays.</summary>
        public sealed class Flipbook : IDisposable
        {
            public readonly List<string> Frames;
            public readonly double Fps;
            Texture2D texture;

            /// <summary>The frame shown last (-1: none yet).</summary>
            public int Shown { get; private set; } = -1;

            Flipbook(List<string> frames, double fps)
            {
                Frames = frames;
                Fps = fps;
            }

            /// <summary>The frames in <paramref name="dir"/> (*.jpg, by name), or null when there are none.</summary>
            public static Flipbook Open(string dir, double fps)
            {
                try
                {
                    if (string.IsNullOrEmpty(dir) || !Directory.Exists(dir)) return null;
                    var frames = new List<string>(Directory.GetFiles(dir, "*.jpg"));
                    if (frames.Count == 0) return null;
                    frames.Sort(StringComparer.Ordinal);
                    return new Flipbook(frames, fps > 0 ? fps : 2);
                }
                catch (Exception)
                {
                    return null;
                }
            }

            public double Length => Frames.Count / Fps;

            public int IndexAt(double seconds) => Math.Max(0, Math.Min(Frames.Count - 1, (int)Math.Floor(seconds * Fps + 1e-9)));

            /// <summary>The picture at that time (read when it is another frame); null when it can't be read.</summary>
            public Texture2D Show(double seconds)
            {
                var i = IndexAt(seconds);
                if (i == Shown && texture != null) return texture;
#if AUTOPILOT_IMAGECONVERSION
                try
                {
                    if (texture == null) texture = new Texture2D(2, 2, TextureFormat.RGB24, false) { hideFlags = HideFlags.HideAndDontSave, name = "Autopilot recording" };
                    if (!ImageConversion.LoadImage(texture, File.ReadAllBytes(Frames[i]))) return null;
                    Shown = i;
                    return texture;
                }
                catch (Exception)
                {
                    return null;
                }
#else
                return null;
#endif
            }

            public void Dispose()
            {
                if (texture != null) UnityEngine.Object.DestroyImmediate(texture);
                texture = null;
                Shown = -1;
            }
        }

        /// <summary>The scrub line: a 2 px track (#24262b), the accent up to the time shown; pressed or dragged, it
        /// seeks there.</summary>
        sealed class Scrub : VisualElement
        {
            readonly VisualElement fill;
            float value;
            bool dragging;
            public float highValue;
            public event Action<float> Changed;

            public Scrub(float high)
            {
                highValue = high;
                AddToClassList("rec-scrub");
                var track = new VisualElement();
                track.AddToClassList("rec-track");
                track.pickingMode = PickingMode.Ignore;
                fill = new VisualElement();
                fill.AddToClassList("rec-fill");
                fill.pickingMode = PickingMode.Ignore;
                fill.style.width = Length.Percent(0);
                track.Add(fill);
                Add(track);
                RegisterCallback<PointerDownEvent>(e =>
                {
                    dragging = true;
                    this.CapturePointer(e.pointerId);
                    SeekTo(e.localPosition.x);
                });
                RegisterCallback<PointerMoveEvent>(e =>
                {
                    if (dragging) SeekTo(e.localPosition.x);
                });
                RegisterCallback<PointerUpEvent>(e =>
                {
                    dragging = false;
                    if (this.HasPointerCapture(e.pointerId)) this.ReleasePointer(e.pointerId);
                });
            }

            public float Value => value;

            void SeekTo(float x)
            {
                var w = contentRect.width;
                if (w <= 0) return;
                SetValueWithoutNotify(Mathf.Clamp01(x / w) * highValue);
                Changed?.Invoke(value);
            }

            public void SetValueWithoutNotify(float v)
            {
                value = Mathf.Clamp(v, 0, Mathf.Max(0.0001f, highValue));
                fill.style.width = Length.Percent(highValue > 0 ? 100f * value / highValue : 0);
            }
        }

        /// <summary>A play mark (#e6e6e8, 30 px; 26 in a narrow window) in the box's middle while nothing has been
        /// shown yet; a warning mark (amber, the same size) in its place once the recording can't play or is gone.</summary>
        sealed class PlayMark : VisualElement
        {
            bool warning;

            public bool Warning
            {
                get => warning;
                set
                {
                    if (warning == value) return;
                    warning = value;
                    MarkDirtyRepaint();
                }
            }

            public PlayMark()
            {
                AddToClassList("rec-mark");
                pickingMode = PickingMode.Ignore;
                generateVisualContent += Draw;
            }

            void Draw(MeshGenerationContext mgc)
            {
                var r = contentRect;
                if (r.width <= 0 || r.height <= 0) return;
                // (the design's M7 4.5v15l12.5-7.5z in a 24 px box drawn at 30 px, 26 when narrow)
                var size = r.width < 420 ? 26f : 30f;
                var s = size / 24f;
                var o = r.center - new Vector2(size, size) / 2f;
                var p = mgc.painter2D;
                if (warning)
                {
                    // (a triangle and its exclamation mark, stroked 1.9 in the 24 px box)
                    p.strokeColor = Amber;
                    p.lineWidth = 1.9f * s;
                    p.lineJoin = LineJoin.Round;
                    p.lineCap = LineCap.Round;
                    p.BeginPath();
                    p.MoveTo(o + new Vector2(12, 3.5f) * s);
                    p.LineTo(o + new Vector2(21.5f, 20.5f) * s);
                    p.LineTo(o + new Vector2(2.5f, 20.5f) * s);
                    p.ClosePath();
                    p.Stroke();
                    p.BeginPath();
                    p.MoveTo(o + new Vector2(12, 9.5f) * s);
                    p.LineTo(o + new Vector2(12, 13.5f) * s);
                    p.MoveTo(o + new Vector2(12, 17) * s);
                    p.LineTo(o + new Vector2(12.01f, 17) * s);
                    p.Stroke();
                    return;
                }
                p.fillColor = Text;
                p.BeginPath();
                p.MoveTo(o + new Vector2(7, 4.5f) * s);
                p.LineTo(o + new Vector2(19.5f, 12) * s);
                p.LineTo(o + new Vector2(7, 19.5f) * s);
                p.ClosePath();
                p.Fill();
            }
        }
    }
}

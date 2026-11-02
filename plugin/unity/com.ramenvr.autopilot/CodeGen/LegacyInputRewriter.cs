// Points the game's calls to UnityEngine.Input's key, button, axis and mouse reads at Autopilot.Playtest.LegacyInput
// (Runtime/LegacyInput.cs), which answers as UnityEngine.Input would plus what the bridge's hands hold during a run.
// The legacy Input Manager reads the operating system's own events, so nothing can be queued on it: this is the only way
// keys reach a game that reads it.
//
// An IL post-processor: Unity runs it on each assembly it compiles, after the compiler (it finds post-processors only in
// assemblies named Unity.*.CodeGen, hence this one's name). It rewrites only assemblies that get the bridge's runtime
// too (its define constraint: the editor, Development builds, AUTOPILOT_PLAYTEST), never Unity's own or the package's
// (Unity.*, UnityEngine.*, UnityEditor.*, Autopilot.*), and none with the scripting define AUTOPILOT_NO_LEGACY_INPUT.
// Each call keeps its arguments; only its target changes, to the member of LegacyInput with the same name and
// signature. A rewritten assembly gets [assembly: LegacyInputRewritten(<calls>)], which hello and context count.
// Anything going wrong leaves the assembly as the compiler made it, with a warning.
//
// The same pass points the game's IMGUI calls (GUI's and GUILayout's Label, Box, Button, RepeatButton, Toggle, TextField
// and TextArea: GuiRouted) at Autopilot.Playtest.ImguiGUI / ImguiGUILayout (Runtime/Imgui.cs), which draw them as Unity
// does and note them for screen, ui and ui_act. Each such call gets one more argument, pushed just before it: the name
// of the class whose code makes it (the script drawing the control), which the ids start with.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Mono.Cecil;
using Mono.Cecil.Cil;
using Mono.Cecil.Rocks;
using Unity.CompilationPipeline.Common.Diagnostics;
using Unity.CompilationPipeline.Common.ILPostProcessing;

namespace Autopilot.Playtest.CodeGen
{
    sealed class LegacyInputRewriter : ILPostProcessor
    {
        const string Runtime = "Autopilot.Playtest";
        const string Shim = "LegacyInput";

        // UnityEngine.Input's members LegacyInput has, by name and parameter types
        static readonly HashSet<string> Routed = new HashSet<string>
        {
            "GetKey(UnityEngine.KeyCode)", "GetKeyDown(UnityEngine.KeyCode)", "GetKeyUp(UnityEngine.KeyCode)",
            "GetKey(System.String)", "GetKeyDown(System.String)", "GetKeyUp(System.String)",
            "GetMouseButton(System.Int32)", "GetMouseButtonDown(System.Int32)", "GetMouseButtonUp(System.Int32)",
            "GetButton(System.String)", "GetButtonDown(System.String)", "GetButtonUp(System.String)",
            "GetAxis(System.String)", "GetAxisRaw(System.String)",
            "get_mousePosition()", "get_mouseScrollDelta()", "get_anyKey()", "get_anyKeyDown()", "get_inputString()",
            "ResetInputAxes()",
        };

        // GUI's and GUILayout's controls the shims have (Runtime/Imgui.cs: the same overloads, with the caller's class
        // name last), by name and parameter types
        internal static readonly HashSet<string> GuiRouted = GuiSignatures();

        static HashSet<string> GuiSignatures()
        {
            const string rect = "UnityEngine.Rect", str = "System.String", tex = "UnityEngine.Texture", content = "UnityEngine.GUIContent";
            const string style = "UnityEngine.GUIStyle", flag = "System.Boolean", count = "System.Int32", options = "UnityEngine.GUILayoutOption[]";
            var set = new HashSet<string>();
            string Sig(string kind, string name, params string[] types) => kind + "::" + name + "(" + string.Join(",", types) + ")";
            foreach (var name in new[] { "Label", "Box", "Button", "RepeatButton" })
            {
                foreach (var what in new[] { str, tex, content })
                {
                    set.Add(Sig("UnityEngine.GUI", name, rect, what));
                    set.Add(Sig("UnityEngine.GUI", name, rect, what, style));
                    set.Add(Sig("UnityEngine.GUILayout", name, what, options));
                    set.Add(Sig("UnityEngine.GUILayout", name, what, style, options));
                }
            }
            foreach (var what in new[] { str, tex, content })
            {
                set.Add(Sig("UnityEngine.GUI", "Toggle", rect, flag, what));
                set.Add(Sig("UnityEngine.GUI", "Toggle", rect, flag, what, style));
                set.Add(Sig("UnityEngine.GUILayout", "Toggle", flag, what, options));
                set.Add(Sig("UnityEngine.GUILayout", "Toggle", flag, what, style, options));
            }
            foreach (var name in new[] { "TextField", "TextArea" })
            {
                set.Add(Sig("UnityEngine.GUI", name, rect, str));
                set.Add(Sig("UnityEngine.GUI", name, rect, str, count));
                set.Add(Sig("UnityEngine.GUI", name, rect, str, style));
                set.Add(Sig("UnityEngine.GUI", name, rect, str, count, style));
                set.Add(Sig("UnityEngine.GUILayout", name, str, options));
                set.Add(Sig("UnityEngine.GUILayout", name, str, count, options));
                set.Add(Sig("UnityEngine.GUILayout", name, str, style, options));
                set.Add(Sig("UnityEngine.GUILayout", name, str, count, style, options));
            }
            return set;
        }

        public override ILPostProcessor GetInstance() => this;

        public override bool WillProcess(ICompiledAssembly compiledAssembly)
        {
            var name = compiledAssembly.Name;
            if (name == "UnityEngine" || name == "UnityEditor" || name.StartsWith("Unity.", StringComparison.Ordinal)
                || name.StartsWith("UnityEngine.", StringComparison.Ordinal) || name.StartsWith("UnityEditor.", StringComparison.Ordinal)
                || name.StartsWith("Autopilot.", StringComparison.Ordinal))
                return false;
            var defines = compiledAssembly.Defines ?? Array.Empty<string>();
            if (defines.Contains("AUTOPILOT_NO_LEGACY_INPUT")) return false;
            // (the runtime's define constraint: where LegacyInput is compiled too)
            return defines.Contains("UNITY_EDITOR") || defines.Contains("DEVELOPMENT_BUILD") || defines.Contains("AUTOPILOT_PLAYTEST");
        }

        public override ILPostProcessResult Process(ICompiledAssembly compiledAssembly)
        {
            var diagnostics = new List<DiagnosticMessage>();
            try
            {
                return Rewrite(compiledAssembly, diagnostics);
            }
            catch (Exception e)
            {
                diagnostics.Add(new DiagnosticMessage
                {
                    DiagnosticType = DiagnosticType.Warning,
                    MessageData = $"Autopilot couldn't route {compiledAssembly.Name}'s UnityEngine.Input reads through the playtest bridge "
                                  + $"(its keys won't reach them): {e.GetType().Name}: {e.Message}",
                });
                return new ILPostProcessResult(compiledAssembly.InMemoryAssembly, diagnostics);
            }
        }

        static ILPostProcessResult Rewrite(ICompiledAssembly compiled, List<DiagnosticMessage> diagnostics)
        {
            var pe = compiled.InMemoryAssembly.PeData;
            var pdb = compiled.InMemoryAssembly.PdbData;
            using var resolver = new Resolver(compiled);
            var reading = new ReaderParameters
            {
                AssemblyResolver = resolver,
                ReadingMode = ReadingMode.Immediate,
                ReadSymbols = pdb != null && pdb.Length > 0,
                SymbolStream = pdb != null && pdb.Length > 0 ? new MemoryStream(pdb) : null,
                SymbolReaderProvider = pdb != null && pdb.Length > 0 ? new PortablePdbReaderProvider() : null,
            };
            using var assembly = AssemblyDefinition.ReadAssembly(new MemoryStream(pe), reading);
            var module = assembly.MainModule;
            if (!module.GetTypeReferences().Any(t => t.FullName == "UnityEngine.Input" || t.FullName == "UnityEngine.GUI" || t.FullName == "UnityEngine.GUILayout"))
                return new ILPostProcessResult(compiled.InMemoryAssembly, diagnostics);

            AssemblyNameReference scope = null;
            TypeReference shim = null, guiShim = null, layoutShim = null;
            void Scope()
            {
                if (scope != null) return;
                scope = module.AssemblyReferences.FirstOrDefault(r => r.Name == Runtime);
                if (scope != null) return;
                scope = new AssemblyNameReference(Runtime, new Version(0, 0, 0, 0));
                module.AssemblyReferences.Add(scope);
            }
            var calls = 0;
            var guiCalls = 0;
            foreach (var type in module.GetTypes())
            {
                foreach (var method in type.Methods)
                {
                    if (!method.HasBody) continue;
                    List<Instruction> drawn = null;
                    foreach (var instruction in method.Body.Instructions)
                    {
                        if (instruction.OpCode != OpCodes.Call && instruction.OpCode != OpCodes.Ldftn) continue;
                        if (!(instruction.Operand is MethodReference target) || target.HasThis) continue;
                        var declaring = target.DeclaringType.FullName;
                        if ((declaring == "UnityEngine.GUI" || declaring == "UnityEngine.GUILayout") && instruction.OpCode == OpCodes.Call
                            && GuiRouted.Contains(declaring + "::" + Signature(target)))
                        {
                            (drawn ??= new List<Instruction>()).Add(instruction);
                            continue;
                        }
                        if (declaring != "UnityEngine.Input" || !Routed.Contains(Signature(target))) continue;
                        Scope();
                        shim ??= new TypeReference(Runtime, Shim, module, scope);
                        var routed = new MethodReference(target.Name, target.ReturnType, shim) { HasThis = false };
                        foreach (var p in target.Parameters) routed.Parameters.Add(new ParameterDefinition(p.ParameterType));
                        instruction.Operand = routed;
                        ++calls;
                    }
                    if (drawn == null) continue;
                    // The IMGUI calls: to the shim's overload with the drawing class's name pushed last
                    Scope();
                    guiShim ??= new TypeReference(Runtime, "ImguiGUI", module, scope);
                    layoutShim ??= new TypeReference(Runtime, "ImguiGUILayout", module, scope);
                    var body = method.Body;
                    body.SimplifyMacros();  // (long branches: the instructions added move their targets)
                    var il = body.GetILProcessor();
                    var owner = OwnerOf(type);
                    foreach (var call in drawn)
                    {
                        var target = (MethodReference)call.Operand;
                        var to = target.DeclaringType.FullName == "UnityEngine.GUI" ? guiShim : layoutShim;
                        var routed = new MethodReference(target.Name, target.ReturnType, to) { HasThis = false };
                        foreach (var p in target.Parameters) routed.Parameters.Add(new ParameterDefinition(p.ParameterType));
                        routed.Parameters.Add(new ParameterDefinition(module.TypeSystem.String));
                        var name = il.Create(OpCodes.Ldstr, owner);
                        il.InsertBefore(call, name);
                        // (a branch to the call goes to the name pushed before it; the C# compiler makes none, its
                        // arguments come first)
                        foreach (var other in body.Instructions)
                        {
                            if (other.Operand == call && other != name) other.Operand = name;
                            else if (other.Operand is Instruction[] targets)
                                for (var i = 0; i < targets.Length; ++i)
                                    if (targets[i] == call)
                                        targets[i] = name;
                        }
                        call.Operand = routed;
                        ++guiCalls;
                    }
                    body.OptimizeMacros();
                }
            }
            if (calls == 0 && guiCalls == 0) return new ILPostProcessResult(compiled.InMemoryAssembly, diagnostics);
            void Mark(string name, int count)
            {
                var attribute = new TypeReference(Runtime, name, module, scope);
                var ctor = new MethodReference(".ctor", module.TypeSystem.Void, attribute) { HasThis = true };
                ctor.Parameters.Add(new ParameterDefinition(module.TypeSystem.Int32));
                var mark = new CustomAttribute(ctor);
                mark.ConstructorArguments.Add(new CustomAttributeArgument(module.TypeSystem.Int32, count));
                assembly.CustomAttributes.Add(mark);
            }
            if (calls > 0) Mark("LegacyInputRewrittenAttribute", calls);
            if (guiCalls > 0) Mark("ImguiRewrittenAttribute", guiCalls);

            var peOut = new MemoryStream();
            var pdbOut = new MemoryStream();
            var writing = new WriterParameters
            {
                WriteSymbols = reading.ReadSymbols,
                SymbolStream = reading.ReadSymbols ? pdbOut : null,
                SymbolWriterProvider = reading.ReadSymbols ? new PortablePdbWriterProvider() : null,
            };
            assembly.Write(peOut, writing);
            return new ILPostProcessResult(new InMemoryAssembly(peOut.ToArray(), pdbOut.ToArray()), diagnostics);
        }

        /// <summary>The class a control is drawn by, as ids name it: the class itself, or for code the compiler made
        /// inside one (a lambda, an iterator), that one's.</summary>
        static string OwnerOf(TypeDefinition type)
        {
            var t = type;
            while (t.DeclaringType != null && (t.Name.StartsWith("<", StringComparison.Ordinal) || t.IsNested && t.CustomAttributes.Any(a => a.AttributeType.Name == "CompilerGeneratedAttribute")))
                t = t.DeclaringType;
            return t.Name;
        }

        static string Signature(MethodReference m)
        {
            return m.Name + "(" + string.Join(",", m.Parameters.Select(p => p.ParameterType.FullName)) + ")";
        }

        /// <summary>Finds the assemblies the compiled one references (the compiler's reference paths).</summary>
        sealed class Resolver : IAssemblyResolver
        {
            readonly Dictionary<string, string> paths = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            readonly Dictionary<string, AssemblyDefinition> loaded = new Dictionary<string, AssemblyDefinition>(StringComparer.OrdinalIgnoreCase);

            public Resolver(ICompiledAssembly compiled)
            {
                foreach (var path in compiled.References)
                {
                    var name = Path.GetFileNameWithoutExtension(path);
                    if (!paths.ContainsKey(name)) paths[name] = path;
                }
            }

            public AssemblyDefinition Resolve(AssemblyNameReference name) => Resolve(name, new ReaderParameters(ReadingMode.Deferred));

            public AssemblyDefinition Resolve(AssemblyNameReference name, ReaderParameters parameters)
            {
                if (loaded.TryGetValue(name.Name, out var found)) return found;
                if (!paths.TryGetValue(name.Name, out var path)) return null;
                parameters.AssemblyResolver = this;
                // (read from memory: the file stays free for the compiler)
                found = AssemblyDefinition.ReadAssembly(new MemoryStream(File.ReadAllBytes(path)), parameters);
                loaded[name.Name] = found;
                return found;
            }

            public void Dispose()
            {
                foreach (var a in loaded.Values) a.Dispose();
                loaded.Clear();
            }
        }
    }
}

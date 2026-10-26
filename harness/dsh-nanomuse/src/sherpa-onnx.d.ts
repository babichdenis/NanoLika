/**
 * The slice of `sherpa-onnx-node` this bundle uses.
 *
 * The package ships JavaScript and a native addon, no declarations, and it is not a
 * dependency of this bundle: the host's own tree carries it (it is what SenseVoice
 * runs on), and `build.mjs` keeps it external so that one copy is loaded, the host's.
 * Only the few members below are called, so they are declared narrowly here instead
 * of reaching into the library with `any`.
 *
 * The shapes follow `sherpa-onnx-node/types.js` of 1.13.8.
 */
declare module 'sherpa-onnx-node' {
  /** One block of 16-bit mono audio, already as floats in [-1, 1). */
  export interface Waveform {
    readonly samples: Float32Array
    readonly sampleRate: number
  }

  /** One recording being decoded. */
  export interface OfflineStream {
    acceptWaveform(wave: Waveform): void
  }

  /** A loaded offline model pair. */
  export interface OfflineRecognizer {
    createStream(): OfflineStream
    /** Decode off the main thread; the returned text is the whole utterance. */
    decodeAsync(stream: OfflineStream): Promise<{ readonly text: string }>
  }

  /** Whisper's own model config: two graphs and what to do with them. */
  export interface WhisperModelConfig {
    readonly encoder: string
    readonly decoder: string
    /** A language code, or an empty string to let Whisper decide. */
    readonly language: string
    readonly task: 'transcribe' | 'translate'
    /** -1 keeps the audio's own tail instead of padding it to 30 seconds. */
    readonly tailPaddings: number
  }

  export interface OfflineRecognizerConfig {
    readonly featConfig: { readonly sampleRate: number; readonly featureDim: number }
    readonly modelConfig: {
      readonly whisper: WhisperModelConfig
      readonly tokens: string
      readonly numThreads: number
      readonly provider: 'cpu'
      readonly debug: number
    }
  }

  /** One stretch of speech the detector kept. */
  export interface SpeechSegment {
    readonly samples: Float32Array
  }

  /** Silero's detector config as sherpa passes it on. */
  export interface VadConfig {
    readonly sileroVad: {
      readonly model: string
      readonly threshold: number
      readonly minSpeechDuration: number
      readonly minSilenceDuration: number
      readonly windowSize: number
      readonly maxSpeechDuration: number
    }
    readonly sampleRate: number
    readonly numThreads: number
    readonly provider: 'cpu'
    readonly debug: number
  }

  /** A streaming speech detector over one waveform. */
  export interface Vad {
    acceptWaveform(samples: Float32Array): void
    isEmpty(): boolean
    /** Disable the native external buffer and return samples in JS-owned memory. */
    front(enableExternalBuffer?: boolean): SpeechSegment
    pop(): void
    flush(): void
    reset(): void
  }

  const sherpa: {
    readonly OfflineRecognizer: {
      /** Build the recognizer without holding the main thread for the model's load. */
      createAsync(config: OfflineRecognizerConfig): Promise<OfflineRecognizer>
    }
    readonly Vad: new (config: VadConfig, bufferSizeInSeconds: number) => Vad
  }
  export default sherpa
}

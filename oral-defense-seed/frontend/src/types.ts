export type Mode = "read_aloud" | "listen_repeat" | "dictation";
export type Level = "meaning" | "hint" | "outline" | "revision" | "full_answer";
export type TextRole = "examiner" | Level;
export type RoleModels = Record<TextRole, string>;
export const roleNames: Record<TextRole, string> = {
  examiner: "対話相手",
  revision: "文章修正",
  full_answer: "対話手本（全文案）",
  meaning: "質問の意味",
  hint: "ヒント",
  outline: "回答の骨子",
};
export type TextModel = {
  id: string;
  provider: string;
  model: string;
  endpoint: string;
  mock: boolean;
};
export type Assessment = {
  status: "ok" | "unavailable" | "insufficient_evidence";
  reason_codes: string[];
  limitations: string[];
  phones: {
    word_index: number;
    expected_phone: string;
    start_s: number;
    end_s: number;
    raw_gop: number;
    quality_flags: string[];
  }[];
};
export type DictationResult = {
  kind: "dictation";
  matches: boolean;
  normalization: string;
  changes: { kind: string; expected: string[]; actual: string[] }[];
};
export type Attempt = {
  id: string;
  audio_id: string | null;
  dictation_text: string | null;
  audio_meta: { duration_s?: number };
  result: (Assessment | DictationResult) | null;
};
export type Exercise = {
  id: string;
  mode: Mode;
  reference_text: string;
  reference_hash: string;
  reference_origin: string;
  attempts: Attempt[];
};
export type CoachMessage = {
  id: string;
  level: Level;
  user_note: string;
  response: {
    explanation_ja: string;
    answer_en: string | null;
    needs_user_input: string[];
  };
};
export type Turn = {
  id: string;
  ordinal: number;
  question_en: string;
  basis_note: string;
  confirmed_answer_en: string | null;
  submitted_via: string | null;
  has_recording: boolean;
  follow_up_count: number;
  coach_messages: CoachMessage[];
  exercises: Exercise[];
  assistance: { kind: string }[];
};
export type ConversationState = {
  id: string;
  status: "paused" | "running" | "ended";
  stage: string;
  revision: number;
  turn_id: string | null;
  reference_id: string | null;
  audio_id: string | null;
  playback_id: string | null;
};
export type Session = {
  conversation: ConversationState | null;
  id: string;
  research_brief: string;
  scenario: string;
  status: string;
  turns: Turn[];
  settings: {
    speech_models: SpeechModels;
    text_model: TextModel;
    role_models: Record<TextRole, TextModel>;
  };
};
export type ModelList = {
  models: {
    id: string;
    provider: string;
    model: string;
    name?: string;
    available: boolean;
    reason: string | null;
  }[];
  default_id: string;
  checked_at: string | null;
  source: "cached" | "empty";
  auto_refresh_hours: number;
  next_refresh_at: number | null;
  refresh_error: string | null;
};
export type Capabilities = {
  text: {
    mock: boolean;
    provider: string;
    model: string | null;
    endpoint: string | null;
  };
  tts: { mock: boolean; provider: string; endpoint: string | null };
  pronunciation: { status: string };
  retention: string;
};

export type SpeechRole = "question" | "coach_answer" | "exercise";
export const speechRoleNames: Record<SpeechRole, string> = {
  question: "対話相手",
  coach_answer: "回答案のお手本",
  exercise: "練習用のお手本",
};
export type SpeechModels = Record<SpeechRole, string>;
export type SpeechCatalog = {
  models: {
    id: string;
    name: string;
    available: boolean;
    reason: string | null;
    local: boolean;
  }[];
  defaults: SpeechModels;
};

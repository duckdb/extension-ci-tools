package main

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"strconv"
	"strings"
	"sync"
)

const (
	ansiReset  = "\x1b[0m"
	ansiBlue   = "\x1b[34m"
	ansiYellow = "\x1b[33m"
	ansiRed    = "\x1b[31m"
	ansiGray   = "\x1b[90m"
)

func newLogger(w io.Writer) *slog.Logger {
	return slog.New(&prettyHandler{
		writer: w,
		level:  slog.LevelInfo,
	})
}

func colorizeLevel(level slog.Level) string {
	token := shortLevel(level)
	switch {
	case level >= slog.LevelError:
		return ansiRed + token + ansiReset
	case level >= slog.LevelWarn:
		return ansiYellow + token + ansiReset
	case level <= slog.LevelDebug:
		return ansiGray + token + ansiReset
	default:
		return ansiBlue + token + ansiReset
	}
}

func shortLevel(level slog.Level) string {
	switch {
	case level >= slog.LevelError:
		return "ERR"
	case level >= slog.LevelWarn:
		return "WRN"
	case level <= slog.LevelDebug:
		return "DBG"
	default:
		return "INF"
	}
}

type prettyHandler struct {
	writer io.Writer
	level  slog.Level
	attrs  []slog.Attr
	group  string
	mu     sync.Mutex
}

func (h prettyHandler) Enabled(ctx context.Context, level slog.Level) bool {
	return level >= h.level
}

func (h prettyHandler) Handle(ctx context.Context, record slog.Record) error {
	var b strings.Builder
	b.WriteString(ansiGray)
	b.WriteString(record.Time.Format("15:04"))
	b.WriteString(ansiReset)
	b.WriteByte(' ')
	b.WriteString(colorizeLevel(record.Level))
	if record.Message != "" {
		b.WriteByte(' ')
		b.WriteString(record.Message)
	}

	for _, attr := range h.attrs {
		appendAttr(&b, h.group, attr)
	}
	record.Attrs(func(attr slog.Attr) bool {
		appendAttr(&b, h.group, attr)
		return true
	})

	b.WriteByte('\n')

	h.mu.Lock()
	defer h.mu.Unlock()
	_, err := io.WriteString(h.writer, b.String())
	return err
}

func (h prettyHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	cloned := h
	cloned.attrs = append(append([]slog.Attr{}, h.attrs...), attrs...)
	return &cloned
}

func (h prettyHandler) WithGroup(name string) slog.Handler {
	cloned := h
	if cloned.group == "" {
		cloned.group = name
	} else {
		cloned.group = cloned.group + "." + name
	}
	return &cloned
}

func appendAttr(b *strings.Builder, group string, attr slog.Attr) {
	attr.Value = attr.Value.Resolve()
	if attr.Equal(slog.Attr{}) {
		return
	}
	b.WriteByte(' ')
	if group != "" {
		b.WriteString(group)
		b.WriteByte('.')
	}
	b.WriteString(attr.Key)
	b.WriteByte('=')
	b.WriteString(formatAttrValue(attr.Value.Any()))
}

func formatAttrValue(v any) string {
	s := fmt.Sprint(v)
	if strings.ContainsAny(s, " \t\n\r\"") {
		return strconv.Quote(s)
	}
	return s
}

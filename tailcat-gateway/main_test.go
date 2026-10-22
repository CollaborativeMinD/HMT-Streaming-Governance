package main

import (
	"bufio"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestReadLineBoundaries(t *testing.T) {
	for _, c := range []struct {
		value string
		pass  bool
	}{{"{}\n", true}, {strings.Repeat("x", maxBody-1) + "\n", true}, {strings.Repeat("x", maxBody) + "\n", false}} {
		value, err := readLine(bufio.NewReaderSize(strings.NewReader(c.value), 1024))
		if (err == nil) != c.pass {
			t.Fatalf("bounded frame result: got error %v pass %v", err, c.pass)
		}
		if c.pass && string(value) != c.value {
			t.Fatal("frame changed")
		}
	}
	_, err := readLine(bufio.NewReader(strings.NewReader("{}")))
	if err != io.EOF {
		t.Fatal("unterminated frame accepted")
	}
}
func TestPrivateKeyPermissionsAndExclusiveCreation(t *testing.T) {
	path := filepath.Join(t.TempDir(), "identity.key")
	if err := newKey(path); err != nil {
		t.Fatal(err)
	}
	if err := newKey(path); err == nil {
		t.Fatal("key overwritten")
	}
	if _, err := privateKey(path); err != nil {
		t.Fatal("private key not readable")
	}
	os.Chmod(path, 0644)
	if _, err := privateKey(path); err == nil {
		t.Fatal("world-readable identity accepted")
	}
}
func TestEmptyConfigurationNeverStartsPublicDiscovery(t *testing.T) {
	path := filepath.Join(t.TempDir(), "empty.json")
	os.WriteFile(path, []byte(`{}`), 0600)
	if err := serve(path); err == nil {
		t.Fatal("empty allowlist/relay accepted")
	}
	if err := relay(path); err == nil {
		t.Fatal("empty relay allowlist accepted")
	}
}

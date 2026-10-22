package main

import (
	"fmt"
	"os"
)

// Optional host-private troubleshooting only. The path must stay outside release artifacts.
func privateDiagnostic(format string, args ...any) {
	path := os.Getenv("HMT_PRIVATE_DIAGNOSTIC")
	if path == "" {
		return
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		return
	}
	defer file.Close()
	fmt.Fprintf(file, format+"\n", args...)
}

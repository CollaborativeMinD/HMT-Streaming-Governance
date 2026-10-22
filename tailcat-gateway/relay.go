package main

import (
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"os"
	"time"

	"tailscale.com/derp/derpserver"
	"tailscale.com/tailcfg"
	"tailscale.com/types/key"
)

type RelayConfig struct {
	Listen  string   `json:"listen"`
	Cert    string   `json:"cert"`
	Key     string   `json:"key"`
	Allowed []string `json:"allowed"`
}

func relay(path string) error {
	raw, err := readPrivate(path)
	if err != nil {
		return err
	}
	var cfg RelayConfig
	if err = json.Unmarshal(raw, &cfg); err != nil {
		return err
	}
	if len(cfg.Allowed) == 0 {
		return errors.New("relay allowlist required")
	}
	allowed := make(map[string]bool)
	for _, value := range cfg.Allowed {
		var peer key.NodePublic
		if err = peer.UnmarshalText([]byte(value)); err != nil {
			return err
		}
		allowed[peer.String()] = true
	}
	checker, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return err
	}
	defer checker.Close()
	verify := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" {
			http.Error(w, "denied", 405)
			return
		}
		var request tailcfg.DERPAdmitClientRequest
		if err := json.NewDecoder(io.LimitReader(r.Body, 4096)).Decode(&request); err != nil {
			http.Error(w, "denied", 400)
			return
		}
		json.NewEncoder(w).Encode(tailcfg.DERPAdmitClientResponse{Allow: allowed[request.NodePublic.String()]})
	})
	verifier := &http.Server{Handler: verify, ReadHeaderTimeout: time.Second, ReadTimeout: time.Second, WriteTimeout: time.Second, MaxHeaderBytes: 4096}
	go verifier.Serve(checker)
	defer verifier.Close()
	server := derpserver.New(key.NewNode(), func(string, ...any) {})
	defer server.Close()
	server.SetVerifyClientURL("http://" + checker.Addr().String())
	server.SetVerifyClientURLFailOpen(false)
	mux := http.NewServeMux()
	mux.Handle("/derp", derpserver.Handler(server))
	mux.HandleFunc("/derp/probe", derpserver.ProbeHandler)
	listener := &http.Server{Addr: cfg.Listen, Handler: mux, ReadHeaderTimeout: 2 * time.Second, MaxHeaderBytes: 8192}
	return listener.ListenAndServeTLS(cfg.Cert, cfg.Key)
}

func init() { os.Setenv("TS_NO_LOGS_NO_SUPPORT", "true") }

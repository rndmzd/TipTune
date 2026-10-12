use std::{io, net::TcpListener};

pub fn check_backend_port(address: &str) -> io::Result<()> {
    // Windows uses an exclusive listener. A surviving older sidecar must not
    // be mistaken for the backend belonging to this desktop process.
    TcpListener::bind(address).map(|listener| drop(listener))
}

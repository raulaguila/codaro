//go:build !windows

package tools

import "syscall"

const readFlags = syscall.O_NONBLOCK | syscall.O_NOFOLLOW

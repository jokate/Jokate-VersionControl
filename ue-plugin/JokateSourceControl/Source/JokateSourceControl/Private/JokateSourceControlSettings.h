// Jokate revision control plugin for Unreal Engine.

#pragma once

#include "CoreMinimal.h"
#include "Misc/ScopeLock.h"

/** 포트 설정. Git 플러그인과 같이 SourceControlSettings.ini 에 저장한다. */
class FJokateSourceControlSettings
{
public:
	/** 사용할 포트. <ProjectDir>/.jokate/daemon.json 이 있으면 그 port 를 우선한다. */
	int32 GetPort() const;

	/** ini 에 저장될 포트(데몬 파일을 보지 않은 값). */
	int32 GetConfiguredPort() const;

	void SetPort(int32 InPort);

	/** ini 에서 읽기 / ini 로 쓰기. */
	void LoadSettings();
	void SaveSettings() const;

	/** http://127.0.0.1:<port> */
	FString GetBaseUrl() const;

	/** .jokate/daemon.json 의 port (없으면 0). */
	static int32 ReadDaemonPort();

private:
	static constexpr int32 DefaultPort = 8765;

	mutable FCriticalSection CriticalSection;
	int32 Port = DefaultPort;
};

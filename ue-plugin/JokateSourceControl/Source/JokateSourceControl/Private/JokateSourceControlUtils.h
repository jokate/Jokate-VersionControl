// Jokate revision control plugin for Unreal Engine.

#pragma once

#include "CoreMinimal.h"
#include "JokateSourceControlState.h"

class FJsonObject;

namespace JokateSourceControlUtils
{
	/** 경로를 절대경로 + 슬래시 형태로 정규화한다. */
	FString NormalizeFilename(const FString& InFilename);

	/** 절대경로 → Content 기준 rel (Content 밖이면 빈 문자열). */
	FString ToContentRelative(const FString& InAbsoluteFilename);

	/** Content 기준 rel → 절대경로. */
	FString FromContentRelative(const FString& InRelativePath);

	/** 데몬이 준 상태 문자열 → enum. */
	EJokateFileState ParseState(const FString& InState);

	/** POST /api/states 응답의 states 오브젝트에서 rel 하나의 상태를 읽는다. */
	bool ParseStatesResponse(const TSharedPtr<FJsonObject>& InJson, TMap<FString, EJokateFileState>& OutStates, FString& OutHeadFix);
}

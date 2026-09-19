// Jokate revision control plugin for Unreal Engine.

#pragma once

#include "CoreMinimal.h"

class FJsonObject;
class FJsonValue;

/** 동기 HTTP 결과. */
struct FJokateHttpResult
{
	bool bOk = false;
	int32 StatusCode = 0;
	/** 응답이 JSON 오브젝트일 때만 유효. */
	TSharedPtr<FJsonObject> Json;
	/** 응답이 JSON 배열일 때만 채워진다(예: /api/history). */
	TArray<TSharedPtr<FJsonValue>> JsonArray;
	FText ErrorText;
};

/**
 * 게임 스레드에서도 안전한 동기 HTTP 도우미.
 * 완료 콜백을 HTTP 스레드에서 받도록(CompleteOnHttpThread) 설정하고 FEvent 로 기다린다.
 * 게임 스레드 틱을 기다리지 않으므로 데드락이 없다.
 */
class FJokateHttp
{
public:
	static constexpr float DefaultTimeoutSeconds = 10.0f;
	static constexpr float LongTimeoutSeconds = 60.0f;

	/** GET <BaseUrl><Path> */
	static FJokateHttpResult GetJson(const FString& BaseUrl, const FString& Path, float TimeoutSeconds = DefaultTimeoutSeconds);

	/** POST <BaseUrl><Path> (Body 가 null 이면 빈 오브젝트) */
	static FJokateHttpResult PostJson(const FString& BaseUrl, const FString& Path, const TSharedPtr<FJsonObject>& Body, float TimeoutSeconds = DefaultTimeoutSeconds);

private:
	static FJokateHttpResult Request(const FString& Verb, const FString& BaseUrl, const FString& Path, const FString& Body, float TimeoutSeconds);
};
